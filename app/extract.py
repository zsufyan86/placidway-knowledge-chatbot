"""Turn one PlacidWay HTML page into clean, structured sections.

PlacidWay pages share one template:
  .pack-title-price-header   title, dates and the "Price starting from" figure
  main.article-dt-desc       the article itself (h2/h3 sections, tables, FAQs)
  .pack-about-aside          sidebar facts: location, treatments, packages
Everything else (site header, footer, related-content carousels, popups,
forms, table of contents) is navigation noise and is removed first.

The price lives in the header and sidebar, outside <main>, so those two
regions are kept on purpose. Tables become "Label: value" lines so a price
never loses the label it belongs to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

NOISE_SELECTORS = [
    "script", "style", "noscript", "svg", "iframe", "link", "meta", "img",
    "picture", "video", "source", "form", "input", "select", "textarea",
    "header.site_header", "footer", "#cookie-container", "#menu", ".sidebar-menu",
    ".related-widgets-row", ".logos-widget-sec", ".table-of-contents",
    "[id$='_popup']", ".social-popup-main", ".request-vido-popup-main",
]

# UI labels that are not content.
JUNK_PHRASES = [
    "Share this listing", "Copy link", "Chat with Center", "Read More", "Read Less",
    "Show more", "Show less", "Request your Free Quote", "Get a Free Quote",
]

HEADINGS = {"h1", "h2", "h3"}
LEAF_TAGS = {"p", "li", "dt", "dd", "blockquote", "figcaption", "h4", "h5", "h6", "label"}
CONTAINER_TAGS = {
    "div", "section", "article", "ul", "ol", "dl", "main", "aside", "figure",
    "nav", "tbody", "thead", "header", "details", "summary",
}
BLOCKISH = ["div", "p", "ul", "ol", "table", "section", "article", "h1", "h2", "h3", "li"]

_ws = re.compile(r"\s+")


def norm(text: str) -> str:
    text = text.replace("​", " ").replace("\xa0", " ")
    for junk in JUNK_PHRASES:
        text = text.replace(junk, " ")
    return _ws.sub(" ", text).strip(" |")


def _text(el: Tag) -> str:
    return norm(el.get_text(" "))


def _has_class(el: Tag, name: str) -> bool:
    return name in (el.get("class") or [])


@dataclass
class Section:
    heading: str
    parent: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.parent} > {self.heading}" if self.parent else self.heading


@dataclass
class Page:
    url: str
    page_type: str
    title: str
    overview: list[str]          # header + sidebar facts
    sections: list[Section]


def table_lines(table: Tag) -> list[str]:
    """Rows become 'Header: value; Header: value' when the table has a header row."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [norm(c.get_text(" ")) for c in tr.find_all(["th", "td"], recursive=False)]
        cells = ["" if c.lower() in {"quote", "get quote", "view", "book now"} else c for c in cells]
        if any(cells):
            rows.append((tr, cells))
    if not rows:
        return []

    first_tr, first_cells = rows[0]
    is_header = bool(first_tr.find("th")) or first_tr.find_parent("thead") is not None
    out: list[str] = []
    if is_header:
        headers = first_cells
        drop = {i for i, h in enumerate(headers) if h.lower() in {"actions", "action", ""}}
        for _, cells in rows[1:]:
            # A price row with an empty first column (PlacidWay's price list has
            # one) says nothing about what the price is for: skip it.
            if not cells[0] and 0 not in drop:
                continue
            parts = []
            for i, value in enumerate(cells):
                if i in drop or not value:
                    continue
                label = headers[i] if i < len(headers) else ""
                parts.append(f"{label}: {value}" if label else value)
            if parts:
                out.append("; ".join(parts))
    else:
        for _, cells in rows:
            out.append(" | ".join(c for c in cells if c))
    return out


def _video_line(card: Tag) -> str:
    title = card.find(class_="title")
    bits = [_text(title) if title else ""]
    for cls in ("provider-name", "subtreatment", "exp-loc"):
        el = card.find(class_=cls)
        if el and _text(el):
            bits.append(_text(el))
    return "Video: " + " | ".join(b for b in bits if b)


def _walk(node: Tag, events: list[tuple]) -> None:
    """Emit ('heading', level, text) and ('text', text) events in reading order."""
    buf: list[str] = []

    def flush():
        t = norm(" ".join(buf))
        if t:
            events.append(("text", t))
        buf.clear()

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in HEADINGS:
            flush()
            t = _text(child)
            if t:
                events.append(("heading", int(name[1]), t))
        elif name == "table":
            flush()
            for line in table_lines(child):
                events.append(("text", line))
        elif _has_class(child, "related-videos-card"):
            flush()
            events.append(("text", _video_line(child)))
        elif name in LEAF_TAGS and not child.find(BLOCKISH):
            flush()
            t = _text(child)
            if t:
                events.append(("text", t))
        elif name in CONTAINER_TAGS or name in LEAF_TAGS:
            flush()
            _walk(child, events)
        else:  # inline: span, a, strong, em, button ...
            buf.append(child.get_text(" "))
    flush()


def _sections_from_events(events: list[tuple], page_title: str) -> list[Section]:
    sections: list[Section] = []
    current = Section(heading=page_title)
    h2 = ""
    for ev in events:
        if ev[0] == "heading":
            _, level, text = ev
            if current.lines:
                sections.append(current)
            if level <= 2:
                h2 = text
                current = Section(heading=text)
            else:
                current = Section(heading=text, parent=h2)
        else:
            text = ev[1]
            if not current.lines or current.lines[-1] != text:
                current.lines.append(text)
    if current.lines:
        sections.append(current)
    # Carousel leftovers such as "Related Experiences:" carry no content.
    return [s for s in sections if not re.match(r"^related\b", s.heading, re.I)]


def _header_lines(header: Tag | None) -> list[str]:
    if header is None:
        return []
    parts = [norm(s) for s in header.stripped_strings]
    parts = [p for p in parts if p]
    joined = " | ".join(parts)
    lines = [joined] if joined else []
    # Make the headline price explicit, e.g. "$18,995 | Price starting from".
    m = re.search(r"(\$\s?[\d,]+(?:\.\d+)?)\s*(?:USD)?\s*\|\s*Price starting from", joined, re.I)
    if m:
        lines.append(f"Headline price shown on this page: starting from {m.group(1)} (USD)")
    return lines


def _aside_lines(aside: Tag | None) -> list[str]:
    if aside is None:
        return []
    items = aside.select("li") or [aside]
    lines = []
    for li in items:
        t = _text(li)
        if t and t not in lines:
            lines.append(t)
    return lines


def extract_page(html: str, url: str, page_type: str) -> Page:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = _text(h1) if h1 else (norm(soup.title.get_text()) if soup.title else url)

    for selector in NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    header = soup.select_one(".pack-title-price-header")
    aside = soup.select_one(".pack-about-aside")
    main = (
        soup.select_one("main.article-dt-desc")
        or soup.select_one(".pack-inner-content")
        or soup.find("main")
        or soup.body
        or soup
    )

    overview = [f"Page: {title}", f"Page type: {page_type}"]
    overview += _header_lines(header)
    overview += _aside_lines(aside)

    events: list[tuple] = []
    _walk(main, events)
    sections = _sections_from_events(events, title)
    return Page(url=url, page_type=page_type, title=title, overview=overview, sections=sections)
