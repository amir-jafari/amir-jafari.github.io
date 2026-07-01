#!/usr/bin/env python3
"""
Fetch publications from Google Scholar and update data/publications.json
and data/publications.js (the file the website actually loads).

Usage:
    python scripts/update_publications.py

Uses scholarly with free rotating proxies so GitHub Actions IPs are not
blocked by Google Scholar. Optionally reads SCRAPER_API_KEY from the
environment for more reliable ScraperAPI-based access.
"""
import json
import os
import re
import sys
import time
import signal
from datetime import datetime

SCHOLAR_USER_ID   = "HVfUixQAAAAJ"
_ROOT             = os.path.join(os.path.dirname(__file__), "..")
_DATA_DIR         = os.path.join(_ROOT, "data")
PUBLICATIONS_JSON = os.path.join(_DATA_DIR, "publications.json")
PUBLICATIONS_JS   = os.path.join(_DATA_DIR, "publications.js")
INDEX_HTML        = os.path.join(_ROOT, "index.html")


# ── proxy setup ──────────────────────────────────────────────────────

def _setup_proxy(scholarly_mod, ProxyGenerator):
    """Configure a proxy so Scholar doesn't block GitHub Actions IPs."""
    scraper_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if scraper_key:
        pg = ProxyGenerator()
        pg.ScraperAPI(scraper_key)
        scholarly_mod.use_proxy(pg)
        print("Proxy: ScraperAPI")
        return

    # Fall back to free rotating proxies (no API key required)
    try:
        pg = ProxyGenerator()
        if pg.FreeProxies():
            scholarly_mod.use_proxy(pg)
            print("Proxy: FreeProxies")
            return
    except Exception as e:
        print(f"FreeProxies unavailable ({e}); trying direct.", file=sys.stderr)

    print("WARNING: No proxy configured. Scholar may block CI requests.", file=sys.stderr)


# ── timeout helpers ───────────────────────────────────────────────────

def _alarm(seconds):
    """Set a SIGALRM; pass 0 to cancel."""
    signal.alarm(seconds)

def _timeout_handler(signum, frame):
    raise TimeoutError("Scholar request timed out.")

signal.signal(signal.SIGALRM, _timeout_handler)


# ── main fetch ────────────────────────────────────────────────────────

def fetch_scholar_papers():
    try:
        from scholarly import scholarly, ProxyGenerator
    except ImportError:
        print("ERROR: scholarly not installed. Run: pip install scholarly free-proxy", file=sys.stderr)
        sys.exit(1)

    _setup_proxy(scholarly, ProxyGenerator)
    print(f"Fetching author {SCHOLAR_USER_ID} from Google Scholar …")

    _alarm(120)
    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["publications", "indices"])
    except TimeoutError:
        print("ERROR: Author lookup timed out (Scholar is blocking requests).", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not fetch author: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _alarm(0)

    metrics = {
        "citations": author.get("citedby", 0),
        "h_index":   author.get("hindex",  0),
        "i10_index": author.get("i10index", 0),
    }

    papers = []
    deadline = time.time() + 900  # hard 15-min wall clock across all papers

    for pub in author.get("publications", []):
        if time.time() > deadline:
            print("  15-min limit reached; remaining papers skipped.", file=sys.stderr)
            break
        try:
            _alarm(45)
            filled = scholarly.fill(pub)
            _alarm(0)
        except TimeoutError:
            _alarm(0)
            print(f"  skip (timeout): {pub.get('bib',{}).get('title','')[:60]}", file=sys.stderr)
            continue
        except Exception as exc:
            _alarm(0)
            print(f"  skip (error): {exc}", file=sys.stderr)
            continue

        bib = filled.get("bib", {})
        papers.append({
            "year":      int(bib["pub_year"]) if bib.get("pub_year") else None,
            "title":     bib.get("title", ""),
            "authors":   bib.get("author", ""),
            "venue":     bib.get("venue", bib.get("journal", bib.get("booktitle", ""))),
            "doi":       filled.get("pub_url") or None,
            "type":      "conference" if bib.get("booktitle") else "journal",
            "citations": filled.get("num_citations"),
            "highlight": False,
        })
        time.sleep(2)  # be polite to Scholar

    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    return metrics, papers


# ── merge ─────────────────────────────────────────────────────────────

def merge(existing, new_papers):
    index = {p["title"].lower().strip(): i for i, p in enumerate(existing)}
    added = 0
    for p in new_papers:
        key = p["title"].lower().strip()
        if not key:
            continue
        if key in index:
            # update citation count only
            if p.get("citations") is not None:
                existing[index[key]]["citations"] = p["citations"]
        else:
            existing.append(p)
            index[key] = len(existing) - 1
            added += 1
    existing.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Added {added} new paper(s). Total: {len(existing)}")
    return existing


# ── JS writer ─────────────────────────────────────────────────────────

def _js_value(v, indent=0):
    pad   = "  " * indent
    inner = "  " * (indent + 1)
    if isinstance(v, dict):
        pairs = [f"{inner}{k}: {_js_value(val, indent+1)}" for k, val in v.items()]
        return "{\n" + ",\n".join(pairs) + "\n" + pad + "}"
    if isinstance(v, list):
        items = [f"{inner}{_js_value(item, indent+1)}" for item in v]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    if v is None:   return "null"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


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
    with open(PUBLICATIONS_JS, "w", encoding="utf-8") as f:
        f.write(header + f"window.PUBLICATIONS_DATA = {_js_value(data, 0)};\n")
    print("data/publications.js regenerated.")


def bump_cache_version():
    version = datetime.now().strftime("%Y%m%d")
    with open(INDEX_HTML, encoding="utf-8") as f:
        html = f.read()
    updated = re.sub(r'(publications\.js\?v=)\d+', rf'\g<1>{version}', html)
    if updated != html:
        with open(INDEX_HTML, "w", encoding="utf-8") as f:
            f.write(updated)
        print(f"Cache version bumped to {version}.")


# ── entry point ───────────────────────────────────────────────────────

def main():
    with open(PUBLICATIONS_JSON) as f:
        data = json.load(f)

    metrics, new_papers = fetch_scholar_papers()

    data["metrics"] = metrics
    data["papers"]  = merge(data.get("papers", []), new_papers)

    with open(PUBLICATIONS_JSON, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("data/publications.json updated.")
    write_js(data)
    bump_cache_version()


if __name__ == "__main__":
    main()