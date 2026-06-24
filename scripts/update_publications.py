#!/usr/bin/env python3
"""
Fetch publications from Google Scholar and update data/publications.json
and data/publications.js (the file the website actually loads).

Usage:
    python scripts/update_publications.py

The script merges new papers found on Google Scholar into the existing JSON,
preserving any manual fields (highlight, notes) already in the file,
then regenerates publications.js from the JSON so the website stays in sync.
"""
import json
import os
import re
import sys
import time
from datetime import datetime

SCHOLAR_USER_ID = "HVfUixQAAAAJ"
_ROOT           = os.path.join(os.path.dirname(__file__), "..")
_DATA_DIR       = os.path.join(_ROOT, "data")
PUBLICATIONS_JSON = os.path.join(_DATA_DIR, "publications.json")
PUBLICATIONS_JS   = os.path.join(_DATA_DIR, "publications.js")
INDEX_HTML        = os.path.join(_ROOT, "index.html")


def fetch_scholar_papers():
    try:
        from scholarly import scholarly
    except ImportError:
        print("ERROR: 'scholarly' not installed. Run: pip install scholarly", file=sys.stderr)
        sys.exit(1)

    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError("Google Scholar request timed out (likely blocked). Use Option B to add papers manually.")

    signal.signal(signal.SIGALRM, _timeout_handler)

    print(f"Fetching publications for user {SCHOLAR_USER_ID} …")

    signal.alarm(60)  # 60-second timeout for author lookup
    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["publications", "indices"])
    finally:
        signal.alarm(0)

    metrics = {
        "citations": author.get("citedby", 0),
        "h_index": author.get("hindex", 0),
        "i10_index": author.get("i10index", 0),
    }

    papers = []
    for pub in author.get("publications", []):
        try:
            signal.alarm(30)  # 30-second timeout per paper
            filled = scholarly.fill(pub)
            signal.alarm(0)
            bib = filled.get("bib", {})
            papers.append({
                "year": int(bib.get("pub_year", 0)) if bib.get("pub_year") else None,
                "title": bib.get("title", ""),
                "authors": bib.get("author", ""),
                "venue": bib.get("venue", bib.get("journal", bib.get("booktitle", ""))),
                "doi": filled.get("pub_url", ""),
                "type": _infer_type(bib),
                "citations": filled.get("num_citations", None),
                "highlight": False,
            })
            time.sleep(1)
        except TimeoutError:
            signal.alarm(0)
            print(f"  Skipping (timeout): {pub.get('bib',{}).get('title','')}", file=sys.stderr)
        except Exception as exc:
            signal.alarm(0)
            print(f"  Warning: could not fill pub '{pub.get('bib',{}).get('title','')}': {exc}", file=sys.stderr)

    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    return metrics, papers


def _infer_type(bib):
    if bib.get("booktitle"):
        return "conference"
    if bib.get("journal"):
        return "journal"
    return "journal"


def merge(existing_papers, new_papers):
    """Keep existing entries intact; add new ones not already present."""
    existing_titles = {p["title"].lower().strip() for p in existing_papers}
    added = 0
    for p in new_papers:
        if p["title"].lower().strip() not in existing_titles:
            existing_papers.append(p)
            existing_titles.add(p["title"].lower().strip())
            added += 1
    existing_papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Added {added} new paper(s). Total: {len(existing_papers)}")
    return existing_papers


def _js_value(v, indent=0):
    """Render a Python value as JavaScript (unquoted keys, JS literals)."""
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if isinstance(v, dict):
        pairs = [f"{inner}{k}: {_js_value(val, indent + 1)}" for k, val in v.items()]
        return "{\n" + ",\n".join(pairs) + "\n" + pad + "}"
    if isinstance(v, list):
        items = [f"{inner}{_js_value(item, indent + 1)}" for item in v]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # string — escape backslashes and double quotes
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_js(data):
    header = """\
// ── Publication Data ──────────────────────────────────────────────
// To add a new paper: copy one entry below, fill in the fields, save, and push to GitHub.
// Fields:
//   year      — integer year
//   title     — full paper title
//   authors   — author string
//   venue     — journal or conference name
//   doi       — full URL (or null)
//   type      — "journal" | "conference" | "preprint"
//   citations — integer (or null)
//   highlight — true to show in "Most Cited Works" cards

"""
    body = f"window.PUBLICATIONS_DATA = {_js_value(data, 0)};\n"
    with open(PUBLICATIONS_JS, "w", encoding="utf-8") as f:
        f.write(header + body)
    print("data/publications.js regenerated successfully.")


def bump_cache_version():
    version = datetime.utcnow().strftime("%Y%m%d")
    with open(INDEX_HTML, encoding="utf-8") as f:
        html = f.read()
    updated = re.sub(r'(publications\.js\?v=)\d+', rf'\g<1>{version}', html)
    if updated != html:
        with open(INDEX_HTML, "w", encoding="utf-8") as f:
            f.write(updated)
        print(f"index.html cache version bumped to {version}.")


def main():
    with open(PUBLICATIONS_JSON) as f:
        data = json.load(f)

    metrics, new_papers = fetch_scholar_papers()

    data["metrics"] = metrics
    data["papers"] = merge(data.get("papers", []), new_papers)

    with open(PUBLICATIONS_JSON, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("data/publications.json updated successfully.")
    write_js(data)
    bump_cache_version()


if __name__ == "__main__":
    main()