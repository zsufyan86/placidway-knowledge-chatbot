"""Keyword retrieval (BM25) over the chunks.

Why BM25 and not embeddings: the knowledge base is a few hundred chunks from
7 pages, and the words that matter here are exact (adrenal, anal,
adenocarcinoma, in-patient, Thailand, Turkey). BM25 is exact on those, needs
no second model or vector database, and costs zero API calls, which matters
on the Gemini free tier. Non-English or vague questions are first rewritten
into an English search query by the LLM (see pipeline.py).
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

STOPWORDS = set(
    """a an the and or of to in on for with at by from is are was were be been it its this that
    these those what which who whom how much many does do did can could would should will i you me my
    your we our they their there here about as if than then so not no yes please tell give me any
    some more most also just only into over per""".split()
)

# Small synonym map so "how much / price / fee" all hit the cost sections.
SYNONYMS = {
    "cost": ["price", "usd"],
    "costs": ["price", "usd"],
    "price": ["cost", "usd"],
    "prices": ["cost", "usd"],
    "fee": ["cost", "price"],
    "expensive": ["cost", "price"],
    "cheap": ["cost", "price"],
    "cheaper": ["cost", "price"],
    "inpatient": ["in", "patient"],
    "outpatient": ["out", "patient"],
    "included": ["includes", "include"],
    "include": ["included", "includes"],
    "includes": ["included", "include"],
    "stay": ["duration", "weeks"],
    "long": ["duration", "weeks"],
    "duration": ["weeks"],
    "located": ["location", "address"],
    "where": ["location", "address"],
    "location": ["address"],
    "address": ["location"],
}

# Questions about money: chunks that actually contain a price get a boost, so
# a cost question is not drowned by, say, video titles that share its words.
_COST_INTENT = re.compile(r"\b(cost|costs|price|prices|pricing|how much|fee|fees|cheap|cheaper|expensive|usd|dollars?)\b|\$", re.I)
_HAS_PRICE = re.compile(r"\$\s?\d")

_token = re.compile(r"[a-z0-9]+")

# Words too generic to say WHICH treatment or place a price belongs to.
GENERIC_TERMS = set(
    """price cost usd fee total dollar package program treatment cancer clinic center page
    included include includes starting start list average range low high per session option
    alternative medicine therapy patient itc tijuana mexico""".split()
)
SPECIFIC_MAX_SHARE = 0.3  # a word in more than 30% of chunks is not specific


def tokenize(text: str) -> list[str]:
    tokens = []
    for t in _token.findall(text.lower().replace(",", "")):
        if t in STOPWORDS:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # very light plural folding
        tokens.append(t)
    return tokens


def expand(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for t in tokens:
        out += SYNONYMS.get(t, [])
    return out


class Retriever:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        corpus = [
            tokenize(f"{c['page_title']} {c['heading']} {c['heading']} {c['text']}") for c in chunks
        ]
        self.bm25 = BM25Okapi(corpus) if chunks else None
        self.vocab = {t for doc in corpus for t in doc}
        self.overview_by_url = {c["url"]: c for c in chunks if c["kind"] == "overview"}
        df: dict[str, int] = {}
        for doc in corpus:
            for t in set(doc):
                df[t] = df.get(t, 0) + 1
        limit = max(1, SPECIFIC_MAX_SHARE * len(corpus))
        # 4+ letters: drops scraps such as 'c' and 'n' (accented words split by
        # the tokenizer) and labels such as 'en'/'es' from the language switcher.
        self.specific_vocab = {t for t, n in df.items()
                               if n <= limit and len(t) >= 4 and t.isalpha() and t not in GENERIC_TERMS}
        self._page_vocab: dict[str, set[str]] = {}
        for c, doc in zip(chunks, corpus):
            self._page_vocab.setdefault(c["url"], set()).update(doc)

    def page_terms(self, url: str) -> set[str]:
        """Every word on one page."""
        return self._page_vocab.get(url, set())

    def specific_terms(self, text: str) -> set[str]:
        """Words in `text` that pin down a treatment, place or item, e.g.
        'adrenal', 'turkey', 'ozone'. Used to check a price belongs to it."""
        return {t for t in tokenize(text) if t in self.specific_vocab}

    def coverage(self, query: str) -> float:
        """Share of the query's words that exist anywhere on the pages. Low
        coverage usually means another language or very vague wording."""
        tokens = tokenize(query)
        if not tokens:
            return 0.0
        return sum(t in self.vocab for t in tokens) / len(tokens)

    def search(self, query: str, k: int = 8, max_per_page: int = 4) -> list[tuple[dict, float]]:
        if not self.bm25:
            return []
        q = expand(tokenize(query))
        if not q:
            return []
        scores = list(self.bm25.get_scores(q))
        if _COST_INTENT.search(query):
            scores = [s * (1.3 if _HAS_PRICE.search(c["text"]) else 0.8) for s, c in zip(scores, self.chunks)]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        picked: list[tuple[dict, float]] = []
        per_page: dict[str, int] = {}
        for i in ranked:
            if scores[i] <= 0 or len(picked) >= k:
                break
            c = self.chunks[i]
            if per_page.get(c["url"], 0) >= max_per_page:
                continue
            per_page[c["url"]] = per_page.get(c["url"], 0) + 1
            picked.append((c, float(scores[i])))
        return picked

    def context_for(self, query: str, k: int = 8, max_per_page: int = 4) -> tuple[list[dict], float]:
        """Top chunks plus the overview chunk of every page they come from
        (the overview holds the headline price and location)."""
        hits = self.search(query, k, max_per_page)
        top_score = hits[0][1] if hits else 0.0
        chosen: list[dict] = [c for c, _ in hits]
        ids = {c["id"] for c in chosen}
        for c, _ in hits:
            ov = self.overview_by_url.get(c["url"])
            if ov and ov["id"] not in ids:
                chosen.append(ov)
                ids.add(ov["id"])
        return chosen, top_score
