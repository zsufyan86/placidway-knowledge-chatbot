import httpx

from app import config
from app.chunker import chunk_page
from app.crawler import fetch_all
from app.extract import extract_page
from app.retrieve import Retriever, tokenize
from conftest import fixture_html


def _all_text(chunks):
    return "\n".join(c["heading"] + "\n" + c["text"] for c in chunks)


def test_noise_is_removed_and_prices_keep_labels():
    page = extract_page(fixture_html("package"), "https://x/p", "Treatment package")
    text = _all_text(chunk_page(page, 0))
    # navigation, cookie banner, popups, forms, related carousels, footer: gone
    for junk in ["We use cookies", "Book a video call", "Turkey $999", "footer links",
                 "Table of contents", "Select a treatment", "Share this listing", "Copy link"]:
        assert junk not in text
    # header price survives and is made explicit
    assert "starting from $18,995" in text
    # table rows keep their column labels
    assert "Program Type: In-Patient Program; Duration: 3 Weeks; Total Cost (USD): $30,000" in text
    # sidebar facts are kept
    assert "Blvd. Padre Kino 10133" in text
    # an empty "Related Experiences" heading does not become a chunk
    assert "Related Experiences" not in text


def test_chunks_follow_page_sections():
    page = extract_page(fixture_html("package"), "https://x/p", "Treatment package")
    chunks = chunk_page(page, 3)
    headings = [c["heading"] for c in chunks]
    assert headings[0] == "Page overview"
    assert "Cost of Adrenal Cancer Treatment in Tijuana, Mexico" in headings
    faq = next(c for c in chunks if c["heading"].startswith("FAQs"))
    assert "Is English spoken at the clinic?" in faq["text"] and "staff speaks English" in faq["text"]
    # the optional extra cost is its own chunk, not mixed into the FAQ
    extra = next(c for c in chunks if c["heading"].startswith("Optional"))
    assert "$2,600" in extra["text"] and "$2,600" not in faq["text"]
    assert all(c["id"].startswith("p3-") for c in chunks)


def test_video_cards_become_one_line_each():
    page = extract_page(fixture_html("videos"), "https://x/v", "Clinic videos")
    text = _all_text(chunk_page(page, 0))
    assert "Video: Breast Cancer Treatment Journey at ITC | Cancer Treatment | ITC Immunity Therapy Center | Tijuana, Mexico" in text
    # card titles must not split the page into one chunk per video
    assert len(chunk_page(page, 0)) == 2


def test_tables_without_header_and_action_cells():
    page = extract_page(fixture_html("pricing"), "https://x/s", "Price comparison")
    text = _all_text(chunk_page(page, 0))
    assert "Fizikon Medical Center | Konya, Turkey | $110 | Ozone Therapy, Alternative Medicine" in text
    assert "| Quote" not in text


def test_crawler_respects_robots_delay_and_disallow():
    robots = "User-agent: *\nDisallow: /private/\nCrawl-delay: 10\n"
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.path, request.headers.get("user-agent")))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, text="<html><body><p>ok</p></body></html>")

    sleeps = []
    urls = ["https://site.test/a", "https://site.test/private/b", "https://site.test/c"]
    res = fetch_all(urls, sleep=sleeps.append, transport=httpx.MockTransport(handler))
    assert [r.status for r in res] == [200, 0, 200]
    assert res[1].error == "disallowed by robots.txt"
    assert "/private/b" not in [p for p, _ in seen]
    assert sleeps == [10]  # Crawl-delay honoured, only between real fetches
    assert all(ua == config.USER_AGENT for _, ua in seen)


def test_refresh_keeps_old_kb_when_a_page_fails(kb):
    before = len(kb.kb["chunks"])

    def broken_fetch(urls):
        from app.crawler import FetchResult
        return [FetchResult(u, 503, "", "HTTP 503") for u in urls]

    result = kb.refresh(fetch=broken_fetch)
    assert result["status"] == "kept previous knowledge base"
    assert len(kb.kb["chunks"]) == before


def test_retrieval_finds_cost_section(kb):
    hits = kb.retriever.search("how much does the adrenal treatment cost", k=3)
    assert hits and hits[0][0]["heading"].startswith("Cost of Adrenal")
    chosen, _ = kb.retriever.context_for("adrenal cost")
    assert any(c["kind"] == "overview" for c in chosen)  # headline price page attached


def test_retrieval_per_page_cap():
    chunks = [{"id": f"p0-c{i}", "url": "u0", "page_title": "A", "page_type": "t", "heading": "h",
               "kind": "section", "text": "ozone therapy price"} for i in range(6)]
    chunks.append({"id": "p1-c0", "url": "u1", "page_title": "B", "page_type": "t", "heading": "h",
                   "kind": "section", "text": "ozone therapy"})
    chunks += [{"id": f"p2-c{i}", "url": "u2", "page_title": "C", "page_type": "t", "heading": "h",
                "kind": "section", "text": f"unrelated text number {i}"} for i in range(20)]
    hits = Retriever(chunks).search("ozone therapy price", k=8, max_per_page=4)
    assert sum(c["url"] == "u0" for c, _ in hits) == 4
    assert any(c["url"] == "u1" for c, _ in hits)


def test_tokenize_and_coverage(kb):
    assert "18995" in tokenize("$18,995")
    assert kb.retriever.coverage("adrenal cancer cost") == 1.0
    assert kb.retriever.coverage("ما هي تكلفة العلاج") == 0.0
