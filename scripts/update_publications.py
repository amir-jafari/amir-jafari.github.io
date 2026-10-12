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
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

SCRIPT_VERSION    = "v12-dedupe-enrich"   # bump to verify correct version is running
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


# ── title matching ────────────────────────────────────────────────────

def _norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def same_title(a, b):
    """True if two titles are the same paper despite Scholar's quirks.

    Handles case/punctuation ("multi-agent" vs "multi agent"), junk Scholar
    appends ("...: M. Fagbenro et al."), truncation ("...Ambient Wave...")
    and small typos ("Marduard" vs "Marquardt").
    """
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 30 and long_.startswith(short) and len(short) >= 0.75 * len(long_):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.95


def _has_doi(p):
    return (p.get("doi") or "").startswith("https://doi.org/")


# ── merge ─────────────────────────────────────────────────────────────

def dedupe(papers):
    """Collapse entries that are the same paper, keeping the best-filled one."""
    kept = []
    for p in papers:
        twin = next((k for k in kept if same_title(k["title"], p["title"])), None)
        if twin is None:
            kept.append(p)
            continue
        # Prefer a doi.org link, then any non-Scholar link (i.e. curated data).
        rank = lambda x: (_has_doi(x), bool(x.get("doi")) and not x["doi"].startswith(SCHOLAR_BASE))
        keep, drop = (twin, p) if rank(twin) >= rank(p) else (p, twin)
        cites = [c for c in (keep.get("citations"), drop.get("citations")) if c is not None]
        keep["citations"] = max(cites) if cites else None
        keep["highlight"] = keep.get("highlight") or drop.get("highlight")
        if keep is p:
            kept[kept.index(twin)] = p
        print(f"Merged duplicate: {drop['title'][:70]}")
    return kept


def merge(existing, new_papers):
    existing = dedupe(existing)
    updated = set()   # Scholar sometimes lists a paper twice; keep the higher count
    added = 0
    for p in new_papers:
        match = next((i for i, e in enumerate(existing)
                      if same_title(e["title"], p["title"])), None)
        if match is None:
            existing.append(p)
            updated.add(len(existing) - 1)
            added += 1
        elif p.get("citations") is not None:
            old = existing[match].get("citations")
            if match not in updated or old is None or p["citations"] > old:
                existing[match]["citations"] = p["citations"]
            updated.add(match)
    existing.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Added {added} new paper(s). Total: {len(existing)}")
    return existing


# ── Crossref enrichment ───────────────────────────────────────────────

def _crossref_search(title):
    """Crossref records whose title is the same paper; None if the lookup failed."""
    query = urllib.parse.urlencode({"query.bibliographic": title, "rows": 5})
    req = urllib.request.Request(f"{CROSSREF_API}?{query}",
                                 headers={"User-Agent": CROSSREF_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            items = json.load(resp)["message"]["items"]
    except Exception as e:
        print(f"WARNING: Crossref lookup failed for {title[:60]!r} ({type(e).__name__}: {e})")
        return None
    hits = []
    for it in items:
        titles = it.get("title") or []
        titles += [f"{t} {s}" for t in titles for s in it.get("subtitle") or []]
        if any(same_title(t, title) for t in titles):
            hits.append(it)
    return hits


def _is_mine(item):
    # word match: Crossref sometimes files "Amir Hossein Jafari" as family "Hossein Jafari"
    return any(AUTHOR_SURNAME in re.split(r"\W+", (a.get("family") or "").lower())
               for a in item.get("author") or [])


def _strip_tags(text):
    return " ".join(re.sub(r"<[^>]+>", "", text or "").split())


def _cr_authors(item):
    names = []
    for a in item.get("author") or []:
        given = "".join(f"{part[0]}." for part in re.split(r"[\s.]+", a.get("given") or "") if part)
        family = a.get("family") or a.get("name") or ""
        names.append(f"{given} {family}".strip())
    return ", ".join(n for n in names if n)


def _cr_venue(item):
    container = (item.get("container-title") or [""])[0]
    if not container:
        container = ((item.get("institution") or [{}])[0].get("name")
                     or ("SSRN preprint" if item.get("DOI", "").startswith("10.2139/") else "")
                     or ("Preprint" if item.get("type") == "posted-content" else ""))
    parts = [_strip_tags(container)]
    if item.get("volume"): parts.append(f"vol. {item['volume']}")
    if item.get("issue"):  parts.append(f"no. {item['issue']}")
    if item.get("page"):   parts.append(f"pp. {item['page'].replace('-', '–')}")
    return ", ".join(p for p in parts if p)


def _cr_year(item):
    for key in ("published-print", "published-online", "issued", "posted"):
        parts = (item.get(key) or {}).get("date-parts") or [[None]]
        if parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


_CR_TYPES = {"proceedings-article": "conference", "posted-content": "preprint",
             "book-chapter": "conference", "journal-article": "journal"}


def enrich(papers):
    """Fill in DOI and metadata from Crossref; hide entries that aren't yours.

    Skips entries that already have a doi.org link and a year, authors and
    venue. Entries that came straight
    from Scholar (no link, or a Scholar link) get DOI, full authors, venue,
    year and type from Crossref. Hand-entered links are kept, but empty
    fields are still filled. If Crossref shows the paper exists but under
    other authors only, Scholar misattributed it, so the entry is hidden.
    Unresolved entries are retried every run (new papers reach Crossref late).
    """
    filled = hidden = 0
    for p in papers:
        complete = all(p.get(k) for k in ("year", "authors", "venue"))
        if p.get("hidden") or (_has_doi(p) and complete):
            continue
        hits = _crossref_search(p["title"])
        time.sleep(1)   # be polite to the free API
        if not hits:
            continue
        mine = next((h for h in hits if _is_mine(h)), None)
        if mine is None:
            if all(h.get("author") for h in hits):
                p["hidden"] = True
                hidden += 1
                by = _cr_authors(hits[0])
                print(f"Hidden (Crossref lists it under {by[:60]}): {p['title'][:60]}")
            continue

        from_scholar = not p.get("doi") or p["doi"].startswith(SCHOLAR_BASE)
        new = {
            "title":   _strip_tags((mine.get("title") or [""])[0]),
            "authors": _cr_authors(mine),
            "venue":   _cr_venue(mine),
            "year":    _cr_year(mine),
            "type":    _CR_TYPES.get(mine.get("type")),
        }
        for key, value in new.items():
            # type: Scholar's listing can't tell, so new entries all say "journal"
            if value and (from_scholar or key == "type" or not p.get(key)):
                p[key] = value
        if from_scholar:
            p["doi"] = "https://doi.org/" + mine["DOI"]
        filled += 1
        print(f"Filled from Crossref: {p['title'][:60]}")
    papers.sort(key=lambda p: p.get("year") or 0, reverse=True)
    print(f"Crossref: filled {filled}, hid {hidden}.")


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
    enrich(data["papers"])

    with open(PUBLICATIONS_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("data/publications.json updated.")
    write_js(data)
    bump_cache_version()


if __name__ == "__main__":
    main()