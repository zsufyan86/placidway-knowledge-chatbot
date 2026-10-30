import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "KB_PATH", tmp_path / "data" / "kb.json")
    yield


def fixture_html(name: str) -> str:
    return (FIX / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture
def kb():
    """A knowledge base built from the fixture pages, no network."""
    from app.chunker import chunk_page
    from app.extract import extract_page
    from app.kb import KnowledgeBase

    pages = [
        ("package", "https://www.placidway.com/package/7902/adrenal", "Treatment package"),
        ("pricing", "https://www.placidway.com/search-medical-pricings/Alternative-Medicine/All/1", "Price comparison"),
        ("videos", "https://www.placidway.com/profile-videos/5/itc", "Clinic videos"),
    ]
    chunks = []
    for i, (name, url, kind) in enumerate(pages):
        chunks += chunk_page(extract_page(fixture_html(name), url, kind), i)
    k = KnowledgeBase()
    k.set({"built_at": "test", "pages": [], "errors": [], "chunks": chunks})
    return k
