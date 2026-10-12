// ── Publication charts ────────────────────────────────────────────
// Drawn from the same publications data as the list, so they update
// whenever the weekly Scholar sync changes the numbers. Plain SVG, no library.
//
//   Citations per year   ← metrics.citations_by_year (Scholar's histogram)
//   Publications per year ← papers[].year, stacked by papers[].type
//   Most cited papers    ← papers[].citations
//
// Every chart has a hover/focus tooltip and a data-table twin.

const CHART = {
  // Categorical slots, validated on the white card surface (dataviz
  // validate_palette.js: all pairs pass; aqua < 3:1 → legend + table carry it).
  types: [
    { key: 'journal',    label: 'Journal',    color: '#2563eb' },
    { key: 'conference', label: 'Conference', color: '#eb6834' },
    { key: 'preprint',   label: 'Preprint',   color: '#1baf7a' },
  ],
  series: '#2563eb',     // single-series charts
  grid:   '#e9eef5',     // one step off the white surface
  ink:    '#1e293b',     // --text
  muted:  '#64748b',     // --slate
  barMax: 24,            // column thickness cap
  gap:    2,             // surface gap between stacked segments
  radius: 4,             // rounded data-end
};

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function htmlEl(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

// Clean y-axis ticks: steps of 1/2/5 × 10^k, about 4 intervals.
function niceTicks(max) {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw);
  const ticks = [];
  for (let v = 0; v < max + step; v += step) ticks.push(v);
  return ticks;
}

// Column with a rounded top and a square base.
function columnPath(x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}` +
         `H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

// ── shared tooltip ────────────────────────────────────────────────
let chartTip;
function tooltip() {
  if (!chartTip) {
    chartTip = htmlEl('div', 'chart-tip');
    chartTip.setAttribute('role', 'status');
    document.body.appendChild(chartTip);
  }
  return chartTip;
}

// rows: [{ value, label, color? }] — value leads, label follows.
function showTip(anchor, heading, rows) {
  const tip = tooltip();
  tip.replaceChildren(htmlEl('div', 'chart-tip-head', heading));
  rows.forEach(r => {
    const row = htmlEl('div', 'chart-tip-row');
    if (r.color) {
      const key = htmlEl('span', 'chart-tip-key');
      key.style.background = r.color;
      row.appendChild(key);
    }
    row.appendChild(htmlEl('strong', null, r.value));
    row.appendChild(htmlEl('span', null, r.label));
    tip.appendChild(row);
  });
  tip.classList.add('visible');
  const box = anchor.getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = box.left + box.width / 2 - tw / 2 + window.scrollX;
  left = Math.max(8 + window.scrollX, Math.min(left, window.scrollX + document.documentElement.clientWidth - tw - 8));
  let top = box.top + window.scrollY - th - 10;
  if (top < window.scrollY + 8) top = box.bottom + window.scrollY + 10;
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
}

function hideTip() {
  if (chartTip) chartTip.classList.remove('visible');
}

// Wire hover + keyboard focus on a hit target; `marks` lift while active.
function bindTip(hit, marks, heading, rows) {
  const on = () => { marks.forEach(m => m.classList.add('active')); showTip(hit, heading, rows); };
  const off = () => { marks.forEach(m => m.classList.remove('active')); hideTip(); };
  hit.addEventListener('pointerenter', on);
  hit.addEventListener('pointerleave', off);
  hit.addEventListener('focus', on);
  hit.addEventListener('blur', off);
}

// ── data-table twin ───────────────────────────────────────────────
function dataTable(headers, rows) {
  const details = htmlEl('details', 'chart-table');
  details.appendChild(htmlEl('summary', null, 'View data table'));
  const table = htmlEl('table');
  const tr = htmlEl('tr');
  headers.forEach(h => tr.appendChild(htmlEl('th', null, h)));
  table.appendChild(htmlEl('thead')).appendChild(tr);
  const body = table.appendChild(htmlEl('tbody'));
  rows.forEach(r => {
    const row = htmlEl('tr');
    r.forEach(c => row.appendChild(htmlEl('td', null, String(c))));
    body.appendChild(row);
  });
  details.appendChild(table);
  return details;
}

function chartCard(title, subtitle) {
  const card = htmlEl('figure', 'chart-card');
  const cap = htmlEl('figcaption');
  cap.appendChild(htmlEl('h4', 'chart-title', title));
  if (subtitle) cap.appendChild(htmlEl('p', 'chart-sub', subtitle));
  card.appendChild(cap);
  return card;
}

function legend(items) {
  const el = htmlEl('div', 'chart-legend');
  items.forEach(it => {
    const item = htmlEl('span', 'chart-legend-item');
    const sw = htmlEl('span', 'chart-legend-swatch');
    sw.style.background = it.color;
    item.append(sw, document.createTextNode(it.label));
    el.appendChild(item);
  });
  return el;
}

// ── column chart (single or stacked) ──────────────────────────────
// cols: [{ x: label, segs: [{ value, color, label }] }]
// opts: { width, height, labelIdx: Set of column indexes to label on the cap,
//         tip: (col) => [heading, rows], aria: (col) => string }
function columnChart(cols, opts) {
  const W = opts.width, H = opts.height;
  const m = { top: 22, right: 4, bottom: 26, left: 34 };
  const pw = W - m.left - m.right, ph = H - m.top - m.bottom;
  const totals = cols.map(c => c.segs.reduce((s, g) => s + g.value, 0));
  const ticks = niceTicks(Math.max(...totals, 1));
  const yMax = ticks[ticks.length - 1];
  const y = v => m.top + ph - (v / yMax) * ph;

  const svg = svgEl('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: 'chart-svg' });

  ticks.forEach(t => {
    svg.appendChild(svgEl('line', { x1: m.left, x2: W - m.right, y1: y(t), y2: y(t),
      stroke: CHART.grid, 'stroke-width': 1, 'shape-rendering': 'crispEdges' }));
    const lbl = svgEl('text', { x: m.left - 8, y: y(t), class: 'chart-axis', 'text-anchor': 'end',
      'dominant-baseline': 'middle' });
    lbl.textContent = t;
    svg.appendChild(lbl);
  });

  const band = pw / cols.length;
  const bw = Math.min(CHART.barMax, band * 0.62);
  const every = Math.ceil(36 / band);   // thin x labels when columns get narrow

  cols.forEach((col, i) => {
    const cx = m.left + band * i + band / 2;
    const x = cx - bw / 2;
    const marks = [];
    let base = 0;
    const live = col.segs.filter(s => s.value > 0);
    live.forEach((seg, j) => {
      const top = j === live.length - 1;
      const y0 = y(base), y1 = y(base + seg.value);
      const h = Math.max(0, y0 - y1 - (j > 0 ? CHART.gap : 0));
      const yy = y1;
      const path = svgEl('path', {
        d: top ? columnPath(x, yy, bw, h, CHART.radius) : `M${x},${yy}h${bw}v${h}h${-bw}Z`,
        fill: seg.color, class: 'chart-mark',
      });
      svg.appendChild(path);
      marks.push(path);
      base += seg.value;
    });

    if (opts.labelIdx && opts.labelIdx.has(i) && totals[i] > 0) {
      const v = svgEl('text', { x: cx, y: y(totals[i]) - 6, class: 'chart-value', 'text-anchor': 'middle' });
      v.textContent = totals[i];
      svg.appendChild(v);
    }

    // Count from the right so the latest year is always labelled.
    if ((cols.length - 1 - i) % every === 0) {
      const xl = svgEl('text', { x: cx, y: H - 8, class: 'chart-axis', 'text-anchor': 'middle' });
      xl.textContent = col.x;
      svg.appendChild(xl);
    }

    // Hit target: the whole column band, not just the painted pixels.
    const hit = svgEl('rect', { x: m.left + band * i, y: m.top, width: band, height: ph,
      fill: 'transparent', tabindex: 0, class: 'chart-hit', role: 'img',
      'aria-label': opts.aria(col, totals[i]) });
    svg.appendChild(hit);
    const [heading, rows] = opts.tip(col, totals[i]);
    bindTip(hit, marks, heading, rows);
  });

  return svg;
}

// ── the three charts ──────────────────────────────────────────────
function citationsChart(metrics, width) {
  const data = (metrics && metrics.citations_by_year) || [];
  if (!data.length) return null;
  const nowYear = new Date().getFullYear();
  const last = data[data.length - 1];
  const card = chartCard('Citations per year',
    `Google Scholar${last.year === nowYear ? ` · ${nowYear} is year to date` : ''}`);

  let peak = 0;
  data.forEach((d, i) => { if (d.citations > data[peak].citations) peak = i; });
  const cols = data.map(d => ({ x: String(d.year), year: d.year,
    segs: [{ value: d.citations, color: CHART.series, label: 'citations' }] }));

  card.appendChild(columnChart(cols, {
    width, height: 230,
    labelIdx: new Set([peak, data.length - 1]),
    tip: (c, t) => [c.x + (c.year === nowYear ? ' (to date)' : ''),
                    [{ value: t, label: t === 1 ? 'citation' : 'citations' }]],
    aria: (c, t) => `${c.x}: ${t} citations`,
  }));
  card.appendChild(dataTable(['Year', 'Citations'], data.map(d => [d.year, d.citations])));
  return card;
}

function publicationsChart(papers, width) {
  const dated = papers.filter(p => p.year);
  if (!dated.length) return null;
  const years = dated.map(p => p.year);
  const first = Math.min(...years);
  const lastYear = Math.max(...years, new Date().getFullYear());
  const known = new Set(CHART.types.map(t => t.key));
  const typeOf = p => (known.has(p.type) ? p.type : 'journal');

  const cols = [];
  for (let yr = first; yr <= lastYear; yr++) {
    const inYear = dated.filter(p => p.year === yr);
    cols.push({ x: String(yr), segs: CHART.types.map(t => ({
      value: inYear.filter(p => typeOf(p) === t.key).length, color: t.color, label: t.label })) });
  }
  const present = CHART.types.filter(t => dated.some(p => typeOf(p) === t.key));
  const undated = papers.length - dated.length;

  const card = chartCard('Publications per year',
    `${dated.length} papers by type${undated ? ` · ${undated} without a year not shown` : ''}`);
  card.appendChild(legend(present));
  card.appendChild(columnChart(cols, {
    width, height: 230,
    tip: (c, t) => [`${c.x} · ${t} ${t === 1 ? 'paper' : 'papers'}`,
      c.segs.filter(s => s.value > 0).map(s => ({ value: s.value, label: s.label, color: s.color }))],
    aria: (c, t) => `${c.x}: ${t} papers` +
      c.segs.filter(s => s.value).map(s => `, ${s.value} ${s.label.toLowerCase()}`).join(''),
  }));
  card.appendChild(dataTable(['Year', ...present.map(t => t.label), 'Total'],
    cols.map(c => [c.x, ...present.map(t => c.segs.find(s => s.label === t.label).value),
                   c.segs.reduce((s, g) => s + g.value, 0)])));
  return card;
}

function topCitedChart(papers) {
  const top = papers.filter(p => p.citations > 0)
    .sort((a, b) => b.citations - a.citations).slice(0, 6);
  if (!top.length) return null;
  const card = chartCard('Most cited papers', 'Google Scholar citations');
  card.classList.add('chart-wide');
  const max = top[0].citations;
  const list = htmlEl('div', 'chart-hbars');
  top.forEach(p => {
    const row = htmlEl('div', 'chart-hbar');
    row.tabIndex = 0;
    row.setAttribute('aria-label', `${p.title}: ${p.citations} citations`);
    row.appendChild(htmlEl('div', 'chart-hbar-title', p.title));
    const track = htmlEl('div', 'chart-hbar-track');
    const bar = htmlEl('div', 'chart-hbar-bar chart-mark');
    bar.style.width = (p.citations / max * 100) + '%';
    bar.style.background = CHART.series;
    track.append(bar, htmlEl('span', 'chart-hbar-value', p.citations));
    row.appendChild(track);
    bindTip(row, [bar], p.title, [
      { value: p.citations, label: 'citations' },
      { value: p.year || '—', label: p.venue ? p.venue.split(',')[0] : '' },
    ]);
    list.appendChild(row);
  });
  card.appendChild(list);
  card.appendChild(dataTable(['Paper', 'Year', 'Citations'],
    top.map(p => [p.title, p.year || '', p.citations])));
  return card;
}

// Charts are sized to their column's real pixel width (so text stays a
// readable size), and re-drawn when that width changes.
let chartsObserver;
function renderCharts(data) {
  const root = document.getElementById('pub-charts');
  if (!root) return;

  const draw = () => {
    const papers = data.papers || [];
    root.replaceChildren();
    const full = root.clientWidth;
    if (!full) return;
    const twoUp = full >= 760;
    const colW = twoUp ? (full - 24) / 2 : full;
    const inner = Math.floor(colW - 2 * 20 - 2);   // card padding + border

    [citationsChart(data.metrics, inner), publicationsChart(papers, inner), topCitedChart(papers)]
      .filter(Boolean)
      .forEach(c => root.appendChild(c));
    root.classList.toggle('two-up', twoUp);
  };

  if (chartsObserver) chartsObserver.disconnect();
  let lastWidth = -1;
  // Redraw outside the observer callback: drawing changes the root's height,
  // which would otherwise trigger a "ResizeObserver loop" warning.
  chartsObserver = new ResizeObserver(() => setTimeout(() => {
    if (root.clientWidth === lastWidth) return;
    lastWidth = root.clientWidth;
    hideTip();
    draw();
  }, 0));
  chartsObserver.observe(root);
}
