"""Split extracted pages into retrievable chunks.

Chunks follow the page's own structure: one chunk per H2 section, with its
H3 sub-sections (for example each FAQ question) inside it as labelled lines.
That keeps a price table next to its explanation and keeps an FAQ answer next
to its question. Sections longer than MAX_CHARS are split on line boundaries
and keep their heading. Every page also gets an "overview" chunk built from
the header and sidebar, which is where the headline price, location and
treatment list live.
"""

from __future__ import annotations

from .extract import Page, Section

MAX_CHARS = 1400


def _split_lines(lines: list[str], limit: int) -> list[list[str]]:
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for line in lines:
        if len(line) > limit:  # a single very long paragraph
            if cur:
                groups.append(cur)
                cur, size = [], 0
            for i in range(0, len(line), limit):
                groups.append([line[i : i + limit]])
            continue
        if size + len(line) > limit and cur:
            groups.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        groups.append(cur)
    return groups


def _families(sections: list[Section]) -> list[tuple[str, list[str]]]:
    """Group each H2 with the H3 sections under it."""
    fams: list[tuple[str, list[str]]] = []
    for s in sections:
        top = s.parent or s.heading
        lines = ([f"{s.heading}:"] if s.parent else []) + s.lines
        if fams and fams[-1][0] == top:
            fams[-1][1].extend(lines)
        else:
            fams.append((top, list(lines)))
    return fams


def chunk_page(page: Page, page_index: int) -> list[dict]:
    chunks: list[dict] = []

    def add(heading: str, lines: list[str], kind: str):
        for part in _split_lines(lines, MAX_CHARS):
            chunks.append(
                {
                    "id": f"p{page_index}-c{len(chunks)}",
                    "url": page.url,
                    "page_title": page.title,
                    "page_type": page.page_type,
                    "heading": heading,
                    "kind": kind,
                    "text": "\n".join(part),
                }
            )

    add("Page overview", page.overview, "overview")
    for heading, lines in _families(page.sections):
        add(heading, lines, "section")
    return chunks
