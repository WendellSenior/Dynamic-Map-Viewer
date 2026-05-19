const state = {
  events: [],
  snapshots: [],
  coords: { countries: {}, provinces: {} },
  provinces: {},
  sessions: [],
  mapConfig: { width: 1024, height: 512 },
  currentTime: 0,
  filter: 'all',
  tMin: 0,
  tMax: 0,
  allTMin: 0,
  allTMax: 0,
  view: { x: 0, y: 0, scale: 1, minScale: 1 },
};

const SLIDER_RES = 1000;
const MAX_SCALE = 8;

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

function computeMinScale() {
  const c = document.getElementById('map-container');
  return Math.min(
    c.clientWidth / state.mapConfig.width,
    c.clientHeight / state.mapConfig.height,
  );
}

function clampView() {
  const c = document.getElementById('map-container');
  const cw = c.clientWidth, ch = c.clientHeight;
  const mw = state.mapConfig.width * state.view.scale;
  const mh = state.mapConfig.height * state.view.scale;
  state.view.x = mw <= cw ? (cw - mw) / 2 : Math.min(0, Math.max(cw - mw, state.view.x));
  state.view.y = mh <= ch ? (ch - mh) / 2 : Math.min(0, Math.max(ch - mh, state.view.y));
}

function applyTransform() {
  const frame = document.getElementById('map-frame');
  frame.style.width = state.mapConfig.width + 'px';
  frame.style.height = state.mapConfig.height + 'px';
  frame.style.transform =
    `translate(${state.view.x}px, ${state.view.y}px) scale(${state.view.scale})`;
  document.getElementById('event-dots')
    .style.setProperty('--inv-scale', 1 / state.view.scale);
}

function zoomToCoords(coords, scale = 2.0) {
  const c = document.getElementById('map-container');
  const cw = c.clientWidth, ch = c.clientHeight;
  const s = Math.max(state.view.minScale, Math.min(MAX_SCALE, scale));
  state.view.scale = s;
  state.view.x = cw / 2 - coords[0] * s;
  state.view.y = ch / 2 - coords[1] * s;
  clampView();
  const frame = document.getElementById('map-frame');
  frame.style.transition = 'transform 0.35s ease';
  applyTransform();
  setTimeout(() => { frame.style.transition = ''; }, 380);
}

function refitOnResize() {
  const oldMin = state.view.minScale;
  state.view.minScale = computeMinScale();
  // If user was at fit-scale, keep them at fit-scale through resize.
  if (Math.abs(state.view.scale - oldMin) < 0.0005) {
    state.view.scale = state.view.minScale;
  } else {
    state.view.scale = Math.max(state.view.minScale, state.view.scale);
  }
  clampView();
  applyTransform();
}

function wireMapInteractions() {
  const container = document.getElementById('map-container');

  container.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const worldX = (mx - state.view.x) / state.view.scale;
    const worldY = (my - state.view.y) / state.view.scale;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const next = Math.max(state.view.minScale, Math.min(state.view.scale * factor, MAX_SCALE));
    state.view.x = mx - worldX * next;
    state.view.y = my - worldY * next;
    state.view.scale = next;
    clampView();
    applyTransform();
  }, { passive: false });

  let drag = null;
  let dragMoved = false;

  container.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    if (e.target.closest('.event-dot')) return;
    drag = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
    dragMoved = false;
    container.classList.add('dragging');
    e.preventDefault();
  });

  window.addEventListener('mousemove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!dragMoved && Math.abs(dx) + Math.abs(dy) > 4) dragMoved = true;
    state.view.x = drag.vx + dx;
    state.view.y = drag.vy + dy;
    clampView();
    applyTransform();
  });

  window.addEventListener('mouseup', () => {
    if (!drag) return;
    drag = null;
    container.classList.remove('dragging');
  });

  // Suppress click events that follow a real drag, so pans don't double as clicks.
  container.addEventListener('click', e => {
    if (dragMoved) {
      e.stopPropagation();
      e.preventDefault();
      dragMoved = false;
    }
  }, true);

  new ResizeObserver(refitOnResize).observe(container);
}

function parseSessionFilter(f) {
  if (typeof f !== 'string' || !f.startsWith('session:')) return null;
  return parseInt(f.slice(8), 10);
}

function getActiveSession() {
  const idx = parseSessionFilter(state.filter);
  if (idx === null) return null;
  return state.sessions[idx] || null;
}

function isEventVisible(e, t) {
  const eT = parseDate(e.date);
  const session = getActiveSession();
  if (session) {
    if (eT < parseDate(session.start) || eT > parseDate(session.end)) return false;
    return eT <= t;
  }
  if (state.filter === 'past') return eT <= t;
  return true;
}

function applyFilter(newFilter) {
  state.filter = newFilter;
  const session = getActiveSession();
  if (session) {
    state.tMin = parseDate(session.start);
    state.tMax = parseDate(session.end);
  } else {
    state.tMin = state.allTMin;
    state.tMax = state.allTMax;
  }
  state.currentTime = Math.min(state.tMax, Math.max(state.tMin, state.currentTime));
  document.getElementById('timeline').value = timeToSlider(state.currentTime);
  const labels = document.getElementById('timeline-labels');
  labels.innerHTML = state.tMax > state.tMin
    ? `<span>${formatDate(state.tMin)}</span><span>${formatDate(state.tMax)}</span>`
    : '';
  renderTimelineMarks();
  render();
  updateBrowserVisibility();
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
    if (!isEventVisible(e, t)) continue;
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
    const eT = parseDate(e.date);
    if (eT < state.tMin || eT > state.tMax) continue;
    const pct = ((eT - state.tMin) / range) * 100;
    const m = document.createElement('div');
    m.className = 'tl-mark event';
    m.style.left = `${pct}%`;
    m.title = `${e.date} — ${e.snippet || ''}`;
    container.appendChild(m);
  }

  for (const s of state.snapshots) {
    const sT = parseDate(s.date);
    if (sT < state.tMin || sT > state.tMax) continue;
    const pct = ((sT - state.tMin) / range) * 100;
    const m = document.createElement('div');
    m.className = 'tl-mark snapshot';
    m.style.left = `${pct}%`;
    m.title = `${s.date}${s.label ? ' — ' + s.label : ''}`;
    m.addEventListener('click', () => {
      state.currentTime = sT;
      document.getElementById('timeline').value = timeToSlider(state.currentTime);
      render();
      updateBrowserVisibility();
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

  const coords = resolveCoords(e);
  if (coords) {
    const btn = document.createElement('button');
    btn.className = 'jump-to-pin';
    btn.textContent = 'Zoom to map pin';
    btn.addEventListener('click', () => zoomToCoords(coords));
    panel.appendChild(btn);
  }

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

function jumpToEvent(e) {
  const t = parseDate(e.date);
  state.currentTime = t;
  document.getElementById('timeline').value = timeToSlider(t);
  render();
  updateBrowserVisibility();
  showEvent(e);
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

    tr.addEventListener('click', () => jumpToEvent(e));
    tbody.appendChild(tr);
  }
  updateBrowserVisibility();
}

function updateBrowserVisibility() {
  const tbody = document.getElementById('events-tbody');
  if (!tbody) return;
  const t = state.currentTime;
  let visible = 0;
  state.events.forEach((e, i) => {
    const tr = tbody.children[i];
    if (!tr) return;
    const hide = !isEventVisible(e, t);
    tr.hidden = hide;
    if (!hide) visible++;
  });
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
    const [eventsData, snapshotsData, coordsData, provincesData, sessionsData] = await Promise.all([
      loadJSON('data/events.json'),
      loadJSON('data/snapshots.json'),
      loadJSON('data/coords.json'),
      loadJSON('data/reference/eu4/provinces.json').catch(() => ({})),
      loadJSON('data/sessions.json').catch(() => ({ sessions: [] })),
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
    state.sessions = sessionsData.sessions || [];

    const allTimes = [
      ...state.events.map(e => parseDate(e.date)),
      ...state.snapshots.map(s => parseDate(s.date)),
    ];
    if (allTimes.length > 0) {
      state.allTMin = Math.min(...allTimes);
      state.allTMax = Math.max(...allTimes);
      state.tMin = state.allTMin;
      state.tMax = state.allTMax;
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
    if (state.sessions.length > 0) {
      const og = document.createElement('optgroup');
      og.label = 'Sessions';
      state.sessions.forEach((s, idx) => {
        const opt = document.createElement('option');
        opt.value = `session:${idx}`;
        const startYear = (s.start || '').split('-')[0];
        const endYear = (s.end || '').split('-')[0];
        const yrs = startYear && endYear ? ` (${startYear}–${endYear})` : '';
        opt.textContent = `${s.name}${yrs}`;
        og.appendChild(opt);
      });
      filterEl.appendChild(og);
    }
    filterEl.value = state.filter;
    filterEl.addEventListener('change', () => applyFilter(filterEl.value));

    const labels = document.getElementById('timeline-labels');
    if (state.tMax > state.tMin) {
      labels.innerHTML =
        `<span>${formatDate(state.tMin)}</span>` +
        `<span>${formatDate(state.tMax)}</span>`;
    }

    renderTimelineMarks();
    renderBrowser();
    wireTabs();
    wireMapInteractions();
    render();
  } catch (err) {
    document.getElementById('event-panel').innerHTML =
      `<p class="empty">Could not load data: ${err.message}</p>`;
    console.error(err);
  }
}

init();
