#!/usr/bin/env python3
"""
Fetch publications from Google Scholar and update data/publications.json.

Usage:
    python scripts/update_publications.py

The script merges new papers found on Google Scholar into the existing JSON,
preserving any manual fields (highlight, notes) already in the file.
"""
import json
import os
import sys
import time

SCHOLAR_USER_ID = "HVfUixQAAAAJ"
PUBLICATIONS_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "publications.json")


def fetch_scholar_papers():
    try:
        from scholarly import scholarly
    except ImportError:
        print("ERROR: 'scholarly' not installed. Run: pip install scholarly", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching publications for user {SCHOLAR_USER_ID} …")
    author = scholarly.search_author_id(SCHOLAR_USER_ID)
    author = scholarly.fill(author, sections=["publications", "indices"])

    metrics = {
        "citations": author.get("citedby", 0),
        "h_index": author.get("hindex", 0),
        "i10_index": author.get("i10index", 0),
    }

    papers = []
    for pub in author.get("publications", []):
        try:
            filled = scholarly.fill(pub)
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
            time.sleep(1)  # be polite to Scholar
        except Exception as exc:
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


if __name__ == "__main__":
    main()