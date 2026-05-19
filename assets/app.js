const state = {
  events: [],
  snapshots: [],
  coords: { countries: {}, provinces: {} },
  provinces: {},
  mapConfig: { width: 1024, height: 512 },
  currentTime: 0,
  filter: 'all',
  tMin: 0,
  tMax: 0,
};

const SLIDER_RES = 1000;

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to load ${path} (${r.status})`);
  return r.json();
}

function parseDate(s) { return new Date(s + 'T00:00:00Z').getTime(); }

function formatDate(t) {
  const d = new Date(t);
  return d.toISOString().slice(0, 10);
}

function resolveCoords(event) {
  if (Array.isArray(event.coords)) return event.coords;
  if (event.province) {
    if (state.coords.provinces[event.province]) {
      return state.coords.provinces[event.province];
    }
    if (state.provinces[event.province]?.coords) {
      return state.provinces[event.province].coords;
    }
  }
  if (event.country && state.coords.countries[event.country]?.coords) {
    return state.coords.countries[event.country].coords;
  }
  return null;
}

function snapshotForTime(t) {
  if (state.snapshots.length === 0) return null;
  let best = state.snapshots[0];
  for (const s of state.snapshots) {
    if (parseDate(s.date) <= t) best = s;
  }
  return best;
}

function timeToSlider(t) {
  const range = state.tMax - state.tMin;
  if (range <= 0) return 0;
  return Math.round(((t - state.tMin) / range) * SLIDER_RES);
}

function sliderToTime(v) {
  const range = state.tMax - state.tMin;
  return state.tMin + (v / SLIDER_RES) * range;
}

function render() {
  const t = state.currentTime;
  document.getElementById('current-date').textContent = formatDate(t);

  const snap = snapshotForTime(t);
  const img = document.getElementById('map-image');
  if (snap) {
    const target = new URL(snap.image, location.href).href;
    if (img.src !== target) img.src = snap.image;
  }

  const dotsEl = document.getElementById('event-dots');
  dotsEl.replaceChildren();
  for (const e of state.events) {
    const eT = parseDate(e.date);
    if (state.filter === 'past' && eT > t) continue;
    const xy = resolveCoords(e);
    if (!xy) continue;

    const dot = document.createElement('div');
    dot.className = 'event-dot';
    dot.style.left = `${(xy[0] / state.mapConfig.width) * 100}%`;
    dot.style.top = `${(xy[1] / state.mapConfig.height) * 100}%`;
    dot.title = `${e.date} — ${e.snippet || ''}`;
    dot.addEventListener('click', () => showEvent(e));
    dotsEl.appendChild(dot);
  }
}

function renderTimelineMarks() {
  const container = document.getElementById('timeline-marks');
  container.replaceChildren();
  const range = state.tMax - state.tMin;
  if (range <= 0) return;

  for (const e of state.events) {
    const pct = ((parseDate(e.date) - state.tMin) / range) * 100;
    const m = document.createElement('div');
    m.className = 'tl-mark event';
    m.style.left = `${pct}%`;
    m.title = `${e.date} — ${e.snippet || ''}`;
    container.appendChild(m);
  }

  for (const s of state.snapshots) {
    const pct = ((parseDate(s.date) - state.tMin) / range) * 100;
    const m = document.createElement('div');
    m.className = 'tl-mark snapshot';
    m.style.left = `${pct}%`;
    m.title = `${s.date}${s.label ? ' — ' + s.label : ''}`;
    m.addEventListener('click', () => {
      state.currentTime = parseDate(s.date);
      document.getElementById('timeline').value = timeToSlider(state.currentTime);
      render();
    });
    container.appendChild(m);
  }
}

function showEvent(e) {
  const panel = document.getElementById('event-panel');
  const place = [e.country, e.province].filter(Boolean).join(' / ');
  panel.innerHTML = '';

  const h = document.createElement('h2');
  h.textContent = e.date;
  panel.appendChild(h);

  const meta = document.createElement('p');
  meta.className = 'meta';
  meta.textContent = [place, e.author].filter(Boolean).join(' — ');
  panel.appendChild(meta);

  const body = document.createElement('div');
  body.className = 'body';
  body.textContent = e.fullText || e.snippet || '';
  panel.appendChild(body);

  // Sync row selection in the browser table.
  const tbody = document.getElementById('events-tbody');
  if (tbody) {
    tbody.querySelectorAll('tr.selected').forEach(r => r.classList.remove('selected'));
    const row = tbody.querySelector(`tr[data-event-id="${CSS.escape(e.id)}"]`);
    if (row) {
      row.classList.add('selected');
      row.scrollIntoView({ block: 'nearest' });
    }
  }
}

function extractTitle(e) {
  if (!e.fullText) return null;
  for (const line of e.fullText.split('\n')) {
    const s = line.trim();
    if (s.startsWith('# ')) return s.slice(2).trim();
  }
  return null;
}

function renderBrowser() {
  const tbody = document.getElementById('events-tbody');
  tbody.replaceChildren();
  for (const e of state.events) {
    const tr = document.createElement('tr');
    tr.dataset.eventId = e.id;
    tr.dataset.date = e.date;

    const tdDate = document.createElement('td');
    tdDate.className = 'col-date';
    tdDate.textContent = e.date;
    tr.appendChild(tdDate);

    const tdCountry = document.createElement('td');
    tdCountry.className = 'col-country' + (e.country ? '' : ' muted');
    tdCountry.textContent = e.country || '—';
    tr.appendChild(tdCountry);

    const tdProv = document.createElement('td');
    tdProv.className = 'col-province' + (e.province ? '' : ' muted');
    tdProv.textContent = e.province || '—';
    tr.appendChild(tdProv);

    const tdAuthor = document.createElement('td');
    tdAuthor.className = 'col-author';
    tdAuthor.textContent = e.author || '';
    tr.appendChild(tdAuthor);

    const tdSnip = document.createElement('td');
    tdSnip.className = 'col-snippet';
    tdSnip.textContent = extractTitle(e) || e.snippet || '';
    tr.appendChild(tdSnip);

    tr.addEventListener('click', () => showEvent(e));
    tbody.appendChild(tr);
  }
  updateBrowserVisibility();
}

function updateBrowserVisibility() {
  const tbody = document.getElementById('events-tbody');
  if (!tbody) return;
  const t = state.currentTime;
  let visible = 0;
  for (const tr of tbody.children) {
    const eT = parseDate(tr.dataset.date);
    const hide = state.filter === 'past' && eT > t;
    tr.hidden = hide;
    if (!hide) visible++;
  }
  document.getElementById('event-count').textContent = `(${visible})`;
}

function wireTabs() {
  for (const btn of document.querySelectorAll('.tab')) {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.tab-panel').forEach(p => {
        p.classList.toggle('active', p.id === `tab-${target}`);
      });
    });
  }
}

async function init() {
  try {
    const [eventsData, snapshotsData, coordsData, provincesData] = await Promise.all([
      loadJSON('data/events.json'),
      loadJSON('data/snapshots.json'),
      loadJSON('data/coords.json'),
      loadJSON('data/reference/eu4/provinces.json').catch(() => ({})),
    ]);

    state.events = (eventsData.events || []).slice().sort(
      (a, b) => parseDate(a.date) - parseDate(b.date)
    );
    state.snapshots = (snapshotsData.snapshots || []).slice().sort(
      (a, b) => parseDate(a.date) - parseDate(b.date)
    );
    if (snapshotsData.config) Object.assign(state.mapConfig, snapshotsData.config);
    state.coords = { countries: {}, provinces: {}, ...coordsData };
    state.provinces = provincesData;

    const allTimes = [
      ...state.events.map(e => parseDate(e.date)),
      ...state.snapshots.map(s => parseDate(s.date)),
    ];
    if (allTimes.length > 0) {
      state.tMin = Math.min(...allTimes);
      state.tMax = Math.max(...allTimes);
      state.currentTime = state.tMin;
    }

    const slider = document.getElementById('timeline');
    slider.min = 0;
    slider.max = SLIDER_RES;
    slider.step = 1;
    slider.value = 0;
    slider.addEventListener('input', () => {
      state.currentTime = sliderToTime(parseInt(slider.value, 10));
      render();
      if (state.filter === 'past') updateBrowserVisibility();
    });

    const filterEl = document.getElementById('filter');
    filterEl.value = state.filter;
    filterEl.addEventListener('change', () => {
      state.filter = filterEl.value;
      render();
      updateBrowserVisibility();
    });

    const labels = document.getElementById('timeline-labels');
    if (state.tMax > state.tMin) {
      labels.innerHTML =
        `<span>${formatDate(state.tMin)}</span>` +
        `<span>${formatDate(state.tMax)}</span>`;
    }

    renderTimelineMarks();
    renderBrowser();
    wireTabs();
    render();
  } catch (err) {
    document.getElementById('event-panel').innerHTML =
      `<p class="empty">Could not load data: ${err.message}</p>`;
    console.error(err);
  }
}

init();
