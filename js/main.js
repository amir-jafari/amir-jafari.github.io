// ── SPA Page Router ───────────────────────────────────────────────
let pubsLoaded = false;

function navTo(pageId) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById(pageId);
  if (target) target.classList.add('active');
  window.scrollTo(0, 0);
  history.pushState(null, null, '#' + pageId);
  updateNavActive(pageId);
  closeMenu();
  if (pageId === 'publications' && !pubsLoaded) {
    loadPublications();
    pubsLoaded = true;
  }
}

function updateNavActive(pageId) {
  document.querySelectorAll('.nav-links a[data-page]').forEach(a => {
    const match = a.dataset.page === pageId ||
      (pageId.startsWith('pkg-') && a.dataset.page === 'open-source');
    a.classList.toggle('nav-active', match);
  });
}

// Handle browser back / forward buttons
window.addEventListener('popstate', () => {
  const pageId = location.hash.slice(1) || 'home';
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById(pageId);
  if (target) { target.classList.add('active'); updateNavActive(pageId); window.scrollTo(0, 0); }
});

// ── Bootstrap on load ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      navTo(this.dataset.page);
    });
  });

  const pageId = location.hash.slice(1) || 'home';
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = document.getElementById(pageId);
  if (target) {
    target.classList.add('active');
    updateNavActive(pageId);
    if (pageId === 'publications') { loadPublications(); pubsLoaded = true; }
  }
});

// ── Navbar shadow on scroll ────────────────────────────────────────
const navbar = document.getElementById('navbar');
window.addEventListener('scroll', () => {
  navbar.classList.toggle('scrolled', window.scrollY > 20);
}, { passive: true });

// ── Mobile menu ───────────────────────────────────────────────────
function toggleMenu() {
  document.getElementById('navLinks').classList.toggle('open');
}
function closeMenu() {
  document.getElementById('navLinks').classList.remove('open');
}
document.addEventListener('click', e => {
  if (!document.getElementById('navbar').contains(e.target)) closeMenu();
});

// ── Publications — dynamic loader ─────────────────────────────────
// publications.js is cache-busted by the ?v= stamp in index.html, but that only
// helps if the browser re-fetched index.html. A browser holding a cached
// index.html asks for the *old* stamp and renders week-old numbers. So paint
// the embedded copy first (instant, works offline), then re-fetch the JSON with
// no-store and re-render if the server has something newer.
function loadPublications() {
  const container = document.getElementById('pub-list-container');
  if (!container) return;

  const embedded = window.PUBLICATIONS_DATA;
  if (embedded) renderPublications(embedded, container);

  fetch('data/publications.json', { cache: 'no-store' })
    .then(res => (res.ok ? res.json() : Promise.reject(new Error(res.status))))
    .then(fresh => {
      if (!fresh || !Array.isArray(fresh.papers)) throw new Error('bad payload');
      if (embedded && JSON.stringify(fresh) === JSON.stringify(embedded)) return;
      window.PUBLICATIONS_DATA = fresh;
      renderPublications(fresh, container);
    })
    .catch(() => {
      // Offline, file://, or a bad response — the embedded copy is still valid.
      if (!embedded) showPublicationsError(container);
    });
}

function renderPublications(data, container) {
  renderStats(data);
  renderHighlights(data.papers);
  renderFullList(data.papers, container);
}

function showPublicationsError(container) {
  container.innerHTML =
    '<p class="pub-error">Publication data not found. ' +
    '<a href="https://scholar.google.com/citations?user=HVfUixQAAAAJ&hl=en" target="_blank">View on Google Scholar</a>.</p>';
}

function renderStats(data) {
  const el = document.getElementById('pub-stats');
  if (!el || !data.metrics) return;
  const m = data.metrics;
  const total = data.papers ? data.papers.length : '–';
  el.innerHTML = `
    <div class="stat-card"><div class="stat-number">${m.citations}+</div><div class="stat-label">Total Citations</div></div>
    <div class="stat-card"><div class="stat-number">${m.h_index}</div><div class="stat-label">h-index</div></div>
    <div class="stat-card"><div class="stat-number">${m.i10_index}</div><div class="stat-label">i10-index</div></div>
    <div class="stat-card"><div class="stat-number">${total}</div><div class="stat-label">Publications</div></div>
  `;
}

function renderHighlights(papers) {
  const el = document.getElementById('pub-highlights-container');
  if (!el) return;
  const highlights = papers.filter(p => p.highlight);
  if (!highlights.length) { el.style.display = 'none'; return; }
  el.innerHTML = highlights.map(p => `
    <div class="card">
      <div class="card-icon" style="background:linear-gradient(135deg,#f59e0b,#d97706)">
        <i class="fas fa-star"></i>
      </div>
      <h3>${p.title}</h3>
      <p><em>${p.venue}</em><br>${p.authors}</p>
      <div class="card-tags">
        ${p.citations ? `<span class="tag">${p.citations} citations</span>` : ''}
        <span class="tag">${p.year}</span>
      </div>
      ${p.doi ? `<a href="${p.doi}" target="_blank" class="card-link">View Paper <i class="fas fa-arrow-right"></i></a>` : ''}
    </div>
  `).join('');
}

function renderFullList(papers, container) {
  const sorted = [...papers].sort((a, b) => b.year - a.year);
  const typeLabel = { journal: 'Journal Article', conference: 'Conference Paper', preprint: 'Preprint' };

  let html = '<div class="pub-list">';
  sorted.forEach(p => {
    const badge = p.doi
      ? `<a href="${p.doi}" target="_blank" class="pub-badge">DOI</a>`
      : '';
    const citBadge = p.citations
      ? `<span class="pub-badge" style="background:var(--blue)">${p.citations} citations</span>`
      : '';
    const typeBadge = p.type
      ? `<span class="pub-type-badge">${typeLabel[p.type] || p.type}</span>`
      : '';
    html += `
      <div class="pub-item">
        <div class="pub-year">${p.year}</div>
        <div>
          <p class="pub-title">${p.doi
            ? `<a href="${p.doi}" target="_blank">${p.title}</a>`
            : p.title}</p>
          <p class="pub-authors">${p.authors}</p>
          <p class="pub-venue"><em>${p.venue}</em></p>
          <div class="pub-links">${typeBadge}${badge}${citBadge}</div>
        </div>
      </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}