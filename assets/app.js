const state = {
  events: [],
  snapshots: [],
  coords: { countries: {}, provinces: {} },
  mapConfig: { width: 1024, height: 512 },
  currentIndex: 0,
};

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to load ${path} (${r.status})`);
  return r.json();
}

function parseDate(s) { return new Date(s + 'T00:00:00Z').getTime(); }

function resolveCoords(event) {
  if (Array.isArray(event.coords)) return event.coords;
  if (event.province && state.coords.provinces[event.province]) {
    return state.coords.provinces[event.province];
  }
  if (event.country && state.coords.countries[event.country]?.coords) {
    return state.coords.countries[event.country].coords;
  }
  return null;
}

function snapshotForDate(dateStr) {
  if (state.snapshots.length === 0) return null;
  const t = parseDate(dateStr);
  let best = state.snapshots[0];
  for (const s of state.snapshots) {
    if (parseDate(s.date) <= t) best = s;
  }
  return best;
}

function render() {
  const event = state.events[state.currentIndex];
  if (!event) return;

  document.getElementById('current-date').textContent = event.date;

  const snap = snapshotForDate(event.date);
  const img = document.getElementById('map-image');
  if (snap) {
    const target = new URL(snap.image, location.href).href;
    if (img.src !== target) img.src = snap.image;
  }

  const currentT = parseDate(event.date);
  const dotsEl = document.getElementById('event-dots');
  dotsEl.replaceChildren();

  for (const e of state.events) {
    const t = parseDate(e.date);
    if (t > currentT) continue;
    const xy = resolveCoords(e);
    if (!xy) continue;

    const dot = document.createElement('div');
    dot.className = 'event-dot';
    if (e === event) dot.classList.add('recent');
    dot.style.left = `${(xy[0] / state.mapConfig.width) * 100}%`;
    dot.style.top = `${(xy[1] / state.mapConfig.height) * 100}%`;
    dot.title = `${e.date} — ${e.snippet || ''}`;
    dot.addEventListener('click', () => showEvent(e));
    dotsEl.appendChild(dot);
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
}

async function init() {
  try {
    const [eventsData, snapshotsData, coordsData] = await Promise.all([
      loadJSON('data/events.json'),
      loadJSON('data/snapshots.json'),
      loadJSON('data/coords.json'),
    ]);

    state.events = (eventsData.events || []).slice().sort(
      (a, b) => parseDate(a.date) - parseDate(b.date)
    );
    state.snapshots = (snapshotsData.snapshots || []).slice().sort(
      (a, b) => parseDate(a.date) - parseDate(b.date)
    );
    if (snapshotsData.config) Object.assign(state.mapConfig, snapshotsData.config);
    state.coords = { countries: {}, provinces: {}, ...coordsData };

    const slider = document.getElementById('timeline');
    slider.max = Math.max(0, state.events.length - 1);
    slider.addEventListener('input', () => {
      state.currentIndex = parseInt(slider.value, 10);
      render();
    });

    const labels = document.getElementById('timeline-labels');
    if (state.events.length > 0) {
      labels.innerHTML =
        `<span>${state.events[0].date}</span>` +
        `<span>${state.events.at(-1).date}</span>`;
    }

    render();
  } catch (err) {
    document.getElementById('event-panel').innerHTML =
      `<p class="empty">Could not load data: ${err.message}</p>`;
    console.error(err);
  }
}

init();
