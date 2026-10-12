#!/usr/bin/env python3
"""
Fetch publications from Google Scholar and update data/publications.json
and data/publications.js (the file the website actually loads).

Usage:
    python scripts/update_publications.py

Scrapes the public Scholar profile page directly using only the Python
standard library - no scholarly, no paid proxy, nothing to pip install.
Google Scholar blocks datacenter IPs (including GitHub Actions), so this
runs weekly on a home/office PC via scripts/sync_publications.ps1.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

SCRIPT_VERSION    = "v11-crossref-doi"   # bump to verify correct version is running
SCHOLAR_USER_ID   = "HVfUixQAAAAJ"
_ROOT             = os.path.join(os.path.dirname(__file__), "..")
_DATA_DIR         = os.path.join(_ROOT, "data")
PUBLICATIONS_JSON = os.path.join(_DATA_DIR, "publications.json")
PUBLICATIONS_JS   = os.path.join(_DATA_DIR, "publications.js")
INDEX_HTML        = os.path.join(_ROOT, "index.html")

SCHOLAR_BASE = "https://scholar.google.com"
PAGE_SIZE    = 100   # max rows Scholar returns per profile page
USER_AGENT   = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

CROSSREF_API = "https://api.crossref.org/works"
CROSSREF_UA  = ("amir-jafari.github.io publications sync "
                "(https://github.com/amir-jafari/amir-jafari.github.io)")
AUTHOR_SURNAME = "jafari"   # a Crossref match must list this author


# ── profile page parser ───────────────────────────────────────────────

class _ProfileParser(HTMLParser):
    """Pulls the metrics table and publication rows out of a profile page."""

    def __init__(self):
        super().__init__()
        self.stats  = []      # "All" / "Since" cells, row by row
        self.papers = []
        self._row   = None    # paper being built while inside <tr class="gsc_a_tr">
        self._field = None    # which field text is currently going to
        self._grays = 0       # gs_gray divs seen in the current row
        self._skip  = 0       # depth inside <span class="gs_oph"> (year suffix)
        self._buf   = []

    def handle_starttag(self, tag, attrs):
        a   = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "td" and "gsc_rsb_std" in cls:
            self._start("stat")
        elif tag == "tr" and "gsc_a_tr" in cls:
            self._row, self._grays = {"title": "", "authors": "", "venue": "",
                                      "link": None, "citations": "", "year": ""}, 0
        elif self._row is None:
            return
        elif tag == "a" and "gsc_a_at" in cls:
            self._row["link"] = SCHOLAR_BASE + a.get("href", "")
            self._start("title")
        elif tag == "div" and "gs_gray" in cls:
            self._grays += 1
            self._start("authors" if self._grays == 1 else "venue")
        elif tag == "span" and "gs_oph" in cls:
            self._skip += 1
        elif tag == "a" and "gsc_a_ac" in cls:
            self._start("citations")
        elif tag == "span" and "gsc_a_h" in cls:
            self._start("year")

    def handle_endtag(self, tag):
        if tag == "span" and self._skip:
            self._skip -= 1
        elif self._field and tag in ("td", "a", "div", "span"):
            text = " ".join("".join(self._buf).split())
            if self._field == "stat":
                self.stats.append(text)
            else:
                self._row[self._field] = text
            self._field, self._buf = None, []
        elif tag == "tr" and self._row is not None:
            self.papers.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._field and not self._skip:
            self._buf.append(data)

    def _start(self, field):
        self._field, self._buf = field, []


def _fetch_page(cstart):
    url = (f"{SCHOLAR_BASE}/citations?user={SCHOLAR_USER_ID}&hl=en"
           f"&cstart={cstart}&pagesize={PAGE_SIZE}&sortby=pubdate")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = _ProfileParser()
    parser.feed(html)
    return parser


def _int(text):
    text = re.sub(r"\D", "", text or "")
    return int(text) if text else None


def fetch_scholar_papers():
    try:
        first = _fetch_page(0)
    except Exception as e:
        print(f"ERROR: could not reach Google Scholar ({type(e).__name__}: {e}).", file=sys.stderr)
        sys.exit(1)

    # Stats cells are [citations all, since, h all, since, i10 all, since].
    # Missing means Scholar served a CAPTCHA/bot-check page instead of the profile.
    if len(first.stats) < 6:
        print("ERROR: Google Scholar returned a CAPTCHA/bot-detection page "
              "instead of the profile. Is this running on a GitHub-hosted "
              "runner instead of the self-hosted one?", file=sys.stderr)
        sys.exit(1)

    metrics = {
        "citations": _int(first.stats[0]) or 0,
        "h_index":   _int(first.stats[2]) or 0,
        "i10_index": _int(first.stats[4]) or 0,
    }

    rows, page = list(first.papers), first
    while len(page.papers) == PAGE_SIZE:
        time.sleep(3)   # be polite between pages
        page = _fetch_page(len(rows))
        rows.extend(page.papers)

    papers = []
    for r in rows:
        if not r["title"]:
            continue
        papers.append({
            "year":      _int(r["year"]),
            "title":     r["title"],
            "authors":   r["authors"],
            "venue":     r["venue"],
            "doi":       r["link"],
            "type":      "journal",
            "citations": _int(r["citations"]) or 0,
            "highlight": False,
        })

    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Fetched {len(papers)} papers — "
          f"citations={metrics['citations']}, "
          f"h={metrics['h_index']}, i10={metrics['i10_index']}")
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
            if p.get("citations") is not None:
                existing[index[key]]["citations"] = p["citations"]
        else:
            existing.append(p)
            index[key] = len(existing) - 1
            added += 1
    existing.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Added {added} new paper(s). Total: {len(existing)}")
    return existing


# ── DOI lookup ────────────────────────────────────────────────────────

def _norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def find_doi(title):
    """Return a doi.org URL for title from Crossref, or None if no confident match.

    Only accepts a record whose title matches exactly (ignoring case and
    punctuation) and that lists AUTHOR_SURNAME, so a wrong DOI is never filled in.
    """
    query = urllib.parse.urlencode({"query.bibliographic": title, "rows": 5,
                                    "select": "DOI,title,subtitle,author"})
    req = urllib.request.Request(f"{CROSSREF_API}?{query}",
                                 headers={"User-Agent": CROSSREF_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            items = json.load(resp)["message"]["items"]
    except Exception as e:
        print(f"WARNING: Crossref lookup failed for {title[:60]!r} ({type(e).__name__}: {e})")
        return None

    want = _norm_title(title)
    for it in items:
        titles = it.get("title") or []
        titles += [f"{t} {s}" for t in titles for s in it.get("subtitle") or []]
        authors = [(a.get("family") or "").lower() for a in it.get("author") or []]
        if any(_norm_title(t) == want for t in titles) and AUTHOR_SURNAME in authors:
            return "https://doi.org/" + it["DOI"]
    return None


def fill_dois(papers):
    """Look up DOIs for papers with no link or only a Scholar link.

    New papers often aren't in Crossref yet, so unresolved ones are retried
    on every run. Links added by hand (publisher, arXiv, ...) are left alone.
    """
    found = 0
    for p in papers:
        doi = p.get("doi") or ""
        if doi and not doi.startswith(SCHOLAR_BASE):
            continue
        url = find_doi(p["title"])
        if url:
            p["doi"] = url
            found += 1
            print(f"DOI found: {url}  {p['title'][:60]}")
        time.sleep(1)   # be polite to the free API
    print(f"Filled {found} DOI(s).")


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
    with open(PUBLICATIONS_JS, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + f"window.PUBLICATIONS_DATA = {_js_value(data, 0)};\n")
    print("data/publications.js regenerated.")


def bump_cache_version():
    version = datetime.now().strftime("%Y%m%d")
    with open(INDEX_HTML, encoding="utf-8") as f:
        html = f.read()
    updated = re.sub(r'(\.js\?v=)\d+', rf'\g<1>{version}', html)
    if updated != html:
        with open(INDEX_HTML, "w", encoding="utf-8", newline="\n") as f:
            f.write(updated)
        print(f"Cache version bumped to {version}.")


# ── entry point ───────────────────────────────────────────────────────

def main():
    print(f"=== update_publications.py {SCRIPT_VERSION} ===", flush=True)
    with open(PUBLICATIONS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    metrics, new_papers = fetch_scholar_papers()

    data["metrics"] = metrics
    data["papers"]  = merge(data.get("papers", []), new_papers)
    fill_dois(data["papers"])

    with open(PUBLICATIONS_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("data/publications.json updated.")
    write_js(data)
    bump_cache_version()


if __name__ == "__main__":
    main()