"""Command line: crawl the 7 pages and write data/kb.json.

    python -m app.ingest            # crawl and save
    python -m app.ingest --show 2   # also print the chunks of page 2
"""

import argparse
import logging

from .kb import build_kb, diff_pages, load_kb, save_kb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=None, help="print the chunks of page N (0-6)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    old = load_kb()
    kb = build_kb()
    for p in kb["pages"]:
        print(f"  {p['chunks']:>3} chunks  {p['chars']:>6} chars  {p['type']:<18} {p['title'][:70]}")
    for e in kb["errors"]:
        print(f"  ERROR {e['url']}: {e['error']}")
    if not kb["chunks"]:
        print("No content fetched; keeping the previous data/kb.json")
        return
    print(f"Total: {len(kb['chunks'])} chunks. Changed pages: {len(diff_pages(old, kb))}")
    save_kb(kb)
    print("Saved data/kb.json")

    if args.show is not None:
        for c in kb["chunks"]:
            if c["id"].startswith(f"p{args.show}-"):
                print(f"\n--- {c['id']} | {c['heading']}\n{c['text']}")


if __name__ == "__main__":
    main()
