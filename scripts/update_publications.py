#!/usr/bin/env python3
"""
Fetch publications from Google Scholar and update data/publications.json
and data/publications.js (the file the website actually loads).

Usage:
    python scripts/update_publications.py

Runs the scholarly scrape in a child process so it can be hard-killed if
Google Scholar hangs the connection (which it does from CI IP ranges).
Set a repo secret SCRAPER_API_KEY for more reliable access via ScraperAPI.
"""
import json
import multiprocessing
import os
import re
import sys
import tempfile
import time
from datetime import datetime

SCHOLAR_USER_ID   = "HVfUixQAAAAJ"
_ROOT             = os.path.join(os.path.dirname(__file__), "..")
_DATA_DIR         = os.path.join(_ROOT, "data")
PUBLICATIONS_JSON = os.path.join(_DATA_DIR, "publications.json")
PUBLICATIONS_JS   = os.path.join(_DATA_DIR, "publications.js")
INDEX_HTML        = os.path.join(_ROOT, "index.html")


# ── child-process worker ──────────────────────────────────────────────
# Runs in a subprocess so SIGKILL can terminate it even if scholarly
# is stuck in a C-level socket call that ignores SIGALRM.

def _scholar_worker(output_path, scholar_user_id, scraper_api_key):
    """Fetch all Scholar data and dump JSON to output_path."""
    import socket
    import time as _time
    socket.setdefaulttimeout(30)   # every socket op times out after 30 s

    import json as _json

    try:
        from scholarly import scholarly, ProxyGenerator
    except ImportError:
        _json.dump({"error": "scholarly not installed"}, open(output_path, "w"))
        return

    # proxy setup
    if scraper_api_key:
        pg = ProxyGenerator()
        pg.ScraperAPI(scraper_api_key)
        scholarly.use_proxy(pg)
        print("Proxy: ScraperAPI", flush=True)
    else:
        try:
            pg = ProxyGenerator()
            if pg.FreeProxies():
                scholarly.use_proxy(pg)
                print("Proxy: FreeProxies", flush=True)
            else:
                print("WARNING: no free proxies found; trying direct.", flush=True)
        except Exception as e:
            print(f"WARNING: proxy setup failed ({e}); trying direct.", flush=True)

    # author lookup
    try:
        author = scholarly.search_author_id(scholar_user_id)
        author = scholarly.fill(author, sections=["publications", "indices"])
    except Exception as e:
        msg = str(e)
        if "NoneType" in msg or "canonical" in msg or "CAPTCHA" in msg.upper():
            msg = (
                "Google Scholar returned a CAPTCHA/bot-detection page. "
                "Add a SCRAPER_API_KEY repo secret (free at scraperapi.com) "
                "so scholarly can bypass bot detection reliably."
            )
        _json.dump({"error": msg}, open(output_path, "w"))
        return

    metrics = {
        "citations": author.get("citedby", 0),
        "h_index":   author.get("hindex",  0),
        "i10_index": author.get("i10index", 0),
    }

    papers = []
    deadline = _time.time() + 900   # hard 15-min cap across all paper fills

    for pub in author.get("publications", []):
        if _time.time() > deadline:
            print("15-min cap reached; stopping paper fills.", flush=True)
            break
        try:
            filled = scholarly.fill(pub)
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
        except Exception as exc:
            print(f"  skip: {exc}", flush=True)
        _time.sleep(2)

    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    _json.dump({"metrics": metrics, "papers": papers}, open(output_path, "w"))
    print(f"Worker done: {len(papers)} papers fetched.", flush=True)


# ── orchestrator ──────────────────────────────────────────────────────

def fetch_scholar_papers(timeout_sec=1080):   # 18-min timeout (< job timeout)
    scraper_key = os.environ.get("SCRAPER_API_KEY", "").strip()

    fd, output_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)

    try:
        p = multiprocessing.Process(
            target=_scholar_worker,
            args=(output_path, SCHOLAR_USER_ID, scraper_key),
        )
        p.start()
        p.join(timeout=timeout_sec)

        if p.is_alive():
            p.kill()
            p.join()
            print(f"ERROR: Scholar worker killed after {timeout_sec}s.", file=sys.stderr)
            sys.exit(1)

        size = os.path.getsize(output_path)
        if size == 0:
            print("ERROR: Scholar worker wrote no output.", file=sys.stderr)
            sys.exit(1)

        with open(output_path) as f:
            result = json.load(f)

        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)

        metrics = result["metrics"]
        papers  = result["papers"]
        print(f"Fetched {len(papers)} papers — "
              f"citations={metrics['citations']}, "
              f"h={metrics['h_index']}, i10={metrics['i10_index']}")
        return metrics, papers

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


# ── merge ─────────────────────────────────────────────────────────────

def merge(existing, new_papers):
    index = {p["title"].lower().strip(): i for i, p in enumerate(existing)}
    added = 0
    for p in new_papers:
        key = p["title"].lower().strip()
        if not key:
            continue
        if key in index:
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