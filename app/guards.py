"""Deterministic checks that run in code, before and after the model.

A prompt can be argued with; these checks cannot. They are what stops the bot
from showing an invented price, an uncited answer, a foreign link, its own
instructions, or a "yes, it's safe for you" medical verdict.
"""

from __future__ import annotations

import re

# ---------- before the model ----------

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any |the )?(previous|prior|above) ",
    r"(reveal|show|print|repeat|tell me) (me )?(your|the) (system )?(prompt|instructions|rules)",
    r"\bsystem prompt\b",
    r"you are now (a|an|in) ",
    r"\bjailbreak\b|\bdeveloper mode\b|\bDAN\b",
    r"(api[_ ]?key|secret|token|password)s?\b.*\b(show|give|reveal|print)",
    r"(show|give|reveal|print)\b.*\b(api[_ ]?key|secret key|token|password)",
]
_injection = re.compile("|".join(INJECTION_PATTERNS), re.I)


def looks_like_injection(text: str) -> bool:
    return bool(_injection.search(text))


# Questions about the person's own health. "Can I get a quote for my package?"
# must NOT match, so plain "can i" / "my" are not enough on their own.
PERSONAL_MEDICAL = re.compile(
    r"\b(safe|safety|suitable|dangerous|risky|risks?|side effects?|recommend\w*|should i|cure|eligible)\b.*\b(for me|my|i have|i am|i'm|me)\b"
    r"|\b(right|ok|okay|good|work) for (me|my)\b"
    r"|\b(my|i have|i'm|i am)\b.*\b(diabetes|diabetic|pregnan\w*|heart|condition|medication|medicine i take|blood pressure|stage \w+|kidney|liver|allerg\w*)\b",
    re.I,
)


def is_personal_medical(text: str) -> bool:
    return bool(PERSONAL_MEDICAL.search(text))


# ---------- numbers ----------

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# A number with optional thousands separators (comma, dot, thin/nbsp space)
_NUM = r"\d{1,3}(?:[,\.  ]\d{3})+(?:[\.,]\d{1,2})?|\d+(?:[\.,]\d{1,2})?"
_MONEY = re.compile(
    rf"(?:US\$|\$|USD|€|EUR|£|GBP|MXN)\s?(?:{_NUM})|(?:{_NUM})\s?(?:USD|US dollars|dollars|dólares|دولار|€|EUR|MXN)",
    re.I,
)
_BIG_NUMBER = re.compile(rf"(?:{_NUM})")


def _digits(raw: str) -> str:
    """'$18,995.00' -> '18995', '18.995' -> '18995', '$64' -> '64'."""
    raw = raw.translate(_DIGIT_MAP)
    m = re.search(_NUM, raw)
    if not m:
        return ""
    num = m.group(0)
    num = re.sub(r"[\.,]\d{1,2}$", "", num) if re.search(r"\d{1,3}(?:[,\.  ]\d{3})+[\.,]\d{1,2}$", num) else num
    num = re.sub(r"[\.,]00$", "", num)
    return re.sub(r"\D", "", num).lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    """Every money amount, plus every plain number with 3+ digits."""
    text = text.translate(_DIGIT_MAP)
    found = {_digits(m.group(0)) for m in _MONEY.finditer(text)}
    for m in _BIG_NUMBER.finditer(text):
        d = _digits(m.group(0))
        if len(d) >= 3:
            found.add(d)
    found.discard("")
    return found


def all_digit_runs(text: str) -> set[str]:
    """Every number in the source text, normalised, used as the allow-list."""
    text = text.translate(_DIGIT_MAP)
    return {_digits(m.group(0)) for m in _BIG_NUMBER.finditer(text)} - {""}


def unverified_numbers(answer: str, context: str, user_text: str) -> list[str]:
    allowed = all_digit_runs(context) | all_digit_runs(user_text)
    return sorted(n for n in numbers_in(answer) if n not in allowed)


def money_in(text: str) -> set[str]:
    """Only the amounts written with a currency, e.g. '$18,995' -> '18995'."""
    text = text.translate(_DIGIT_MAP)
    return {_digits(m.group(0)) for m in _MONEY.finditer(text)} - {""}


# Clauses: sentences, and "..., while ..." style halves of a comparison.
_CLAUSE = re.compile(r"(?<=[.!?؟。;])\s+|\n+|,\s+(?:and|while|whereas|but)\s+|\s+(?:while|whereas)\s+", re.I)

# Names that must never be swapped for one another: a price for Thailand is
# not a price for Turkey, a price for adrenal cancer is not one for breast cancer.
ENTITY_GROUPS = {
    "place": set("""mexico tijuana guadalajara zapopan puebla cancun turkey konya istanbul thailand bangkok
        india spain germany colombia costa rica panama usa korea japan china poland greece malaysia dubai
        israel brazil peru ecuador argentina canada uk switzerland hungary czech lithuania serbia croatia
        philippines vietnam singapore bahamas dominican jamaica egypt jordan""".split()),
    "cancer": set("""adrenal anal adenocarcinoma breast prostate lung pancreatic colorectal colon liver brain
        ovarian cervical bladder kidney skin melanoma leukemia lymphoma thyroid stomach gastric esophageal
        bone sarcoma uterine testicular""".split()),
}


def _entities(text: str) -> dict[str, set[str]]:
    from .retrieve import tokenize  # local import: retrieve does not depend on guards
    tokens = set(tokenize(text))
    return {g: tokens & names for g, names in ENTITY_GROUPS.items() if tokens & names}


def _figure_lines(chunks: list[dict], figure: str) -> list[tuple[str, str]]:
    """(page url, line) for each source line that contains `figure`. The line
    carries its page title and section heading, which say what it is for."""
    out = []
    for c in chunks:
        for line in c["text"].splitlines():
            if figure in all_digit_runs(line):
                out.append((c["url"], f"{c['page_title']} {c['heading']} {line}"))
    return out


def misattributed_figures(answer: str, chunks: list[dict], specific_terms, page_terms=None) -> list[str]:
    """Prices put next to the wrong treatment, clinic or country.

    For each answer clause with a price, look at the source lines that hold
    that price (with their heading and page title):
      1. If the clause names a country or cancer type and every source line
         that names one of that kind names a different one, it's wrong
         ('In Turkey it costs $64' when $64 is a Bangkok, Thailand clinic).
      2. Otherwise the clause's specific words (adrenal, ozone, LAK ...) must
         appear on such a line, or at least on the same page
         ('adenocarcinoma costs $64' fails: the $64 page never mentions it).
    """
    bad = []
    carried: dict[str, set[str]] = {}
    for clause in _CLAUSE.split(answer):
        terms = specific_terms(clause)
        # "Colon cancer is $18,995. An in-patient program is $30,000." The
        # second clause is still about colon cancer, so the last country /
        # cancer type named carries forward until another one is named.
        ents = {**carried, **_entities(clause)}
        carried = ents
        for fig in money_in(clause):
            lines = _figure_lines(chunks, fig)
            if not lines:
                continue  # missing figures are unverified_numbers' job
            conflict = False
            for group, names in ents.items():
                naming = [_entities(line).get(group) for _, line in lines]
                naming = [n for n in naming if n]
                if naming and not any(n & names for n in naming):
                    conflict = True
            if conflict:
                bad.append(fig)
                continue
            if not terms or any(terms & specific_terms(line) for _, line in lines):
                continue
            if page_terms and any(terms & page_terms(url) for url, _ in lines):
                continue
            bad.append(fig)
    return sorted(set(bad))


def unsupported_premise_figures(question: str, chunks: list[dict], specific_terms,
                                topic_text: str = "") -> list[str]:
    """Prices the user states that the pages do not give for what they asked
    about, e.g. 'I heard adenocarcinoma costs $50' when $50 only appears as a
    consultation fee on another page. `topic_text` (the previous question)
    resolves 'it' in 'I heard it costs $50'."""
    terms = specific_terms(question + " " + topic_text)
    bad = []
    for fig in money_in(question):
        lines = _figure_lines(chunks, fig)
        if not lines or (terms and not any(terms & specific_terms(line) for _, line in lines)):
            bad.append(fig)
    return bad


AGREEMENT = re.compile(
    r"^\W*(yes|yeah|yep|correct|indeed|that'?s (right|correct|true)|that is (right|correct|true)|"
    r"you('re| are) (right|correct)|confirmed|i can confirm|s[ií]|es correcto|correcto|exacto|نعم|صحيح)\b",
    re.I,
)


def agrees_with_unsupported_figure(answer: str, unsupported: list[str]) -> bool:
    """The user quoted a figure the pages don't back, and the answer opens by agreeing."""
    return bool(unsupported) and bool(AGREEMENT.search(answer))


# ---------- links, leaks, medical verdicts ----------

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def scrub_links(answer: str, allowed: set[str]) -> str:
    def md(m):
        return m.group(0) if m.group(2).rstrip("/.") in allowed else m.group(1)

    answer = _MD_LINK.sub(md, answer)
    return _URL.sub(lambda m: m.group(0) if m.group(0).rstrip("/.") in allowed else "", answer).strip()


_SOURCE_MARKER = re.compile(r"\s*\[(?:\d+\s*,\s*)*\d+\]")


def strip_source_markers(answer: str) -> str:
    """'... suitability [3].' -> '... suitability.' Sources are shown as links."""
    return _SOURCE_MARKER.sub("", answer)


LEAK_MARKERS = ["hard rules", "numbered sources given", "you are the placidway website assistant",
                "gemini_api_key", "admin_token"]


def leaks_instructions(answer: str, secrets: list[str]) -> bool:
    low = answer.lower()
    if any(m in low for m in LEAK_MARKERS):
        return True
    return any(s and len(s) > 8 and s in answer for s in secrets)


MEDICAL_VERDICT = re.compile(
    r"\b(it is|it's|this is|treatment is|surgery is)\s+(completely |perfectly |very )?safe for you\b"
    r"|\byou (should|must) (get|have|do|take|book|start)\b|\bhighly recommended for you\b"
    r"|\byes,? (it|this|the treatment) (is|will be) (safe|suitable|fine)\b|\bwill cure\b",
    re.I,
)


def gives_medical_verdict(answer: str) -> bool:
    return bool(MEDICAL_VERDICT.search(answer))
