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
  preloadedImages: [],
  // First-visit default for the quality toggle. localStorage wins for returning
  // users so anyone who switched to 'full' keeps it. Only meaningful when at
  // least one snapshot in snapshots.json has an image_lowres companion — if
  // none do, the toggle stays hidden and this default silently falls through
  // to the full image via imageUrl().
  resolution: 'lowres',
  countryNames: {},
  selectedEventId: null,
};

const SLIDER_RES = 1000;
const MAX_SCALE = 8;
const MAX_DOTS_UNFILTERED = 200;  // cap when filter === 'all' to keep rendering fast on huge campaigns

// Tag → emoji map. Populated from `assets/event-tags.json` on init so the
// canonical list lives in one place. Stays as a const reference, filled by
// loadEventTags() before render() is called. Falls back to '' for unknown
// tags so a missing icon never errors.
const TAG_ICONS = Object.create(null);

async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Failed to load ${path} (${r.status})`);
  return r.json();
}

function countryDisplay(tag, raw) {
  if (!tag) return raw || null;
  return state.countryNames[tag] || raw || tag;
}

function parseDate(s) { return new Date(s + 'T00:00:00Z').getTime(); }

// Hard-coded English month names so the rendered date is locale-independent
// (browsers' built-in formatters can swap to whatever the user's locale is,
// which would look inconsistent across viewers of the same campaign).
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June',
                     'July', 'August', 'September', 'October', 'November', 'December'];

function ordinalSuffix(n) {
  const r100 = n % 100;
  if (r100 >= 11 && r100 <= 13) return 'th';  // 11th, 12th, 13th — never 11st
  switch (n % 10) {
    case 1: return 'st';
    case 2: return 'nd';
    case 3: return 'rd';
    default: return 'th';
  }
}

// Pretty-format a Y/M/D triple — central helper for both date sources.
function formatYMD(year, month, day) {
  return `${day}${ordinalSuffix(day)} ${MONTH_NAMES[month - 1]} ${year}`;
}

// Render an ISO 'YYYY-MM-DD' (the form events.json + snapshots.json store)
// as "1st April 1337". Falls through to the raw string on malformed input
// so debugging stays readable.
function formatEventDate(s) {
  if (!s || typeof s !== 'string') return '—';
  const m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (!m) return s;
  const year = +m[1], month = +m[2], day = +m[3];
  if (month < 1 || month > 12 || day < 1 || day > 31) return s;
  return formatYMD(year, month, day);
}

// Same output for a millisecond timestamp (used by the timeline labels and
// the header current-date). UTC accessors avoid local-timezone drift.
function formatDate(t) {
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  return formatYMD(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
}

function resolveCoords(event) {
  if (Array.isArray(event.coords)) return event.coords;
  if (event.province) {
    const xy = state.provincesIndex[event.province.toLowerCase()];
    if (xy) return xy;
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
  updateDotPositions();
}

// rAF-coalesced render. Called from rapid-fire handlers (slider drag) to avoid
// rebuilding the dot DOM on every input event — at most once per frame.
let _renderScheduled = false;
function scheduleRender() {
  if (_renderScheduled) return;
  _renderScheduled = true;
  requestAnimationFrame(() => {
    _renderScheduled = false;
    render();
    if (state.filter === 'past') updateBrowserVisibility();
  });
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
    // Don't start a pan-drag when clicking a pin, a stack, or its fanned children.
    if (e.target.closest('.event-dot, .event-stack, .stack-overflow, .stack-overflow-list')) return;
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

  // Collapse any open stack fan when clicking outside it (map, dot, panel, etc).
  // Stack/leaf click handlers call stopPropagation, so they don't reach here.
  document.addEventListener('click', () => {
    document.querySelectorAll('.event-stack.expanded').forEach(collapseStack);
  });

  // Esc also collapses.
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.event-stack.expanded').forEach(collapseStack);
    }
  });

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

// A session's `end` may be missing/null/empty → "open-ended", meaning the
// session is still in progress and its right edge tracks the latest known
// event/snapshot (state.allTMax, computed once at init from events.json +
// snapshots.json). Refreshing the page after new events sync widens the range.
function isSessionOpenEnded(session) {
  if (!session) return false;
  const end = session.end;
  if (end === null || end === undefined || end === '') return true;
  return !Number.isFinite(parseDate(end));
}

function sessionEndTime(session) {
  if (isSessionOpenEnded(session)) return state.allTMax;
  return parseDate(session.end);
}

function isEventVisible(e, t) {
  const eT = parseDate(e.date);
  const session = getActiveSession();
  if (session) {
    // Show every event inside the session range — timeline cursor only drives the map snapshot.
    return eT >= parseDate(session.start) && eT <= sessionEndTime(session);
  }
  if (state.filter === 'past') return eT <= t;
  return true;
}

function applyFilter(newFilter) {
  state.filter = newFilter;
  const session = getActiveSession();
  if (session) {
    state.tMin = parseDate(session.start);
    state.tMax = sessionEndTime(session);
  } else {
    state.tMin = state.allTMin;
    state.tMax = state.allTMax;
  }
  state.currentTime = Math.min(state.tMax, Math.max(state.tMin, state.currentTime));
  document.getElementById('timeline').value = timeToSlider(state.currentTime);
  const labels = document.getElementById('timeline-labels');
  // Open-ended sessions show "(ongoing)" so the right label doesn't look like
  // a hardcoded end date.
  const openTag = session && isSessionOpenEnded(session) ? ' <em>(ongoing)</em>' : '';
  labels.innerHTML = state.tMax > state.tMin
    ? `<span>${formatDate(state.tMin)}</span><span>${formatDate(state.tMax)}${openTag}</span>`
    : '';
  renderTimelineMarks();
  render();
  updateBrowserVisibility();
}

function imageUrl(snap) {
  if (state.resolution === 'lowres' && snap.image_lowres) return snap.image_lowres;
  return snap.image;
}

function preloadSnapshots() {
  // Keep Image refs alive so the browser is more likely to hold the decoded bitmaps in memory.
  state.preloadedImages = state.snapshots.map(s => {
    const img = new Image();
    img.decoding = 'async';
    img.src = imageUrl(s);
    img.decode().catch(() => {});
    return img;
  });
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
    const desired = imageUrl(snap);
    const target = new URL(desired, location.href).href;
    if (img.src !== target) img.src = desired;
  }

  const dotsEl = document.getElementById('event-dots');
  dotsEl.replaceChildren();
  // Pick events to draw on map. Respect filters; if filter='all' and there are too
  // many events to render comfortably, draw only the N most recent and surface a hint.
  let toShow = state.events.filter(e => isEventVisible(e, t));
  let cappedTotal = 0;
  if (state.filter === 'all' && toShow.length > MAX_DOTS_UNFILTERED) {
    cappedTotal = toShow.length;
    toShow = toShow.slice(-MAX_DOTS_UNFILTERED);  // events already sorted ascending → tail is most recent
  }
  updateDotCapNote(cappedTotal);

  // Group events by their resolved (x, y) so events at the same pin become one
  // stack badge with a fan-out on click — otherwise events at Paris, Rome, etc.
  // overlap exactly and only the topmost is reachable.
  const groups = new Map();
  for (const e of toShow) {
    const xy = resolveCoords(e);
    if (!xy) continue;
    const key = xy[0] + ',' + xy[1];
    let g = groups.get(key);
    if (!g) {
      g = { xy, events: [] };
      groups.set(key, g);
    }
    g.events.push(e);
  }

  const { x: vx, y: vy, scale: vs } = state.view;
  for (const { xy, events } of groups.values()) {
    if (events.length === 1) {
      dotsEl.appendChild(createEventDot(events[0], xy, vx, vy, vs));
    } else {
      dotsEl.appendChild(createEventStack(events, xy, vx, vy, vs));
    }
  }
  updateMapSelection();  // reapply highlight after the DOM rebuild
}

function createEventDot(e, xy, vx, vy, vs) {
  const dot = document.createElement('div');
  dot.className = 'event-dot';
  const icon = e.tag && TAG_ICONS[e.tag];
  if (icon) {
    dot.classList.add('has-tag');
    dot.dataset.tag = e.tag;
    dot.textContent = icon;
  }
  dot.dataset.mx = xy[0];
  dot.dataset.my = xy[1];
  dot.dataset.eventId = e.id;
  dot.style.left = (vx + xy[0] * vs) + 'px';
  dot.style.top = (vy + xy[1] * vs) + 'px';
  // Custom hover tooltip data. Single dot tooltip format is:
  //   "**<Province>**: <Title or Snippet>"
  // Stacks (multi-event pins) keep the "N events in <Location>" format from
  // showPinTooltip — they have a different _eventCount and no _eventTitle.
  dot._eventCount = 1;
  dot._locationName =
    e.province ||
    countryDisplay(e.country, e.countryRaw) ||
    '(unknown)';
  const titleText = extractTitle(e) || cleanInlineText(e.snippet || '') ||
                    `${formatEventDate(e.date)}${e.tag ? ' · ' + e.tag : ''}`;
  dot._eventTitle = titleText.length > 80 ? titleText.slice(0, 79).trimEnd() + '…' : titleText;
  dot.addEventListener('mouseenter', () => showPinTooltip(dot));
  dot.addEventListener('mouseleave', () => hidePinTooltip());
  dot.addEventListener('click', (ev) => {
    ev.stopPropagation();
    showEvent(e);
  });
  return dot;
}

// Stacks: rendered as a single pin showing the headliner (most recent) event's
// tag icon + a numeric count badge. Click → fan the rest out radially.
const STACK_MAX_FAN = 8;       // visible radial slots before overflow kicks in
const STACK_FAN_RADIUS = 42;   // screen px from stack centre

function createEventStack(events, xy, vx, vy, vs) {
  const stack = document.createElement('div');
  stack.className = 'event-stack';
  stack.dataset.mx = xy[0];
  stack.dataset.my = xy[1];
  // Comma-separated ids of every event in the stack — used by
  // updateMapSelection to highlight the stack when its contained event is selected.
  stack.dataset.eventIds = events.map(ev => ev.id).join(',');
  stack.style.left = (vx + xy[0] * vs) + 'px';
  stack.style.top = (vy + xy[1] * vs) + 'px';
  // Native title attributes don't support HTML, so we use a custom tooltip
  // (see showPinTooltip below) that can bold the location name.
  // Resolve a display name for the stack: use any event's `province` (stacked
  // events share coords and almost always share province text). Fall back to
  // country or "(unknown)" so the tooltip never reads blank.
  const sample = events[0];
  stack._locationName =
    sample.province ||
    countryDisplay(sample.country, sample.countryRaw) ||
    '(unknown)';

  // Headliner icon (most recent). Events are sorted ascending so the last is newest.
  const headliner = events[events.length - 1];
  const icon = headliner.tag && TAG_ICONS[headliner.tag];
  if (icon) {
    stack.classList.add('has-tag');
    const iconEl = document.createElement('span');
    iconEl.className = 'stack-icon';
    iconEl.textContent = icon;
    stack.appendChild(iconEl);
  }

  const badge = document.createElement('span');
  badge.className = 'stack-count';
  badge.textContent = events.length;
  stack.appendChild(badge);

  stack._events = events;  // attach for the click handler
  stack.addEventListener('click', (ev) => {
    ev.stopPropagation();
    if (stack.classList.contains('expanded')) collapseStack(stack);
    else expandStack(stack);
  });
  stack.addEventListener('mouseenter', () => showPinTooltip(stack));
  stack.addEventListener('mouseleave', () => hidePinTooltip());
  return stack;
}

// Custom tooltip for single-event dots, stack pins, and fanned leaves. Lives
// in <body> (so it isn't clipped by the map container's overflow:hidden) and
// is positioned in fixed-coords relative to the pin's bounding rect.
let _pinTooltipEl = null;

function showPinTooltip(pin) {
  hidePinTooltip();
  // Don't show while a fan is open — the leaves themselves are the UI.
  if (pin.classList.contains('expanded')) return;

  const t = document.createElement('div');
  t.className = 'stack-tooltip';

  if (pin._tooltipText) {
    // Plain-text mode (used by fanned leaves): location is implied by the
    // parent stack, so the leaf tooltip just shows its event's title/snippet.
    t.textContent = pin._tooltipText;
  } else if (pin._eventCount === 1 && pin._eventTitle) {
    // Single-dot mode: "<bold Province>: <Title or Snippet>".
    const strong = document.createElement('strong');
    strong.textContent = pin._locationName || '(unknown)';
    t.append(strong, document.createTextNode(': ' + pin._eventTitle));
  } else {
    // Stack mode (or single-dot fallback with no title): "<N> event(s) in <bold Location>".
    const count = pin._eventCount || (pin._events && pin._events.length) || 1;
    const name = pin._locationName || '(unknown)';
    const prefix = document.createTextNode(`${count} ${count === 1 ? 'event' : 'events'} in `);
    const strong = document.createElement('strong');
    strong.textContent = name;
    t.append(prefix, strong);
  }

  const r = pin.getBoundingClientRect();
  // Position above the pin, horizontally centred. translate(-50%,-100%) in
  // CSS pulls the bubble to anchor at this point's bottom-centre.
  t.style.left = (r.left + r.width / 2) + 'px';
  t.style.top = (r.top - 6) + 'px';
  document.body.appendChild(t);
  _pinTooltipEl = t;
}

function hidePinTooltip() {
  if (_pinTooltipEl) {
    _pinTooltipEl.remove();
    _pinTooltipEl = null;
  }
}

function expandStack(stack) {
  // One expanded stack at a time — collapse anything else first.
  document.querySelectorAll('.event-stack.expanded').forEach(s => {
    if (s !== stack) collapseStack(s);
  });
  hidePinTooltip();  // bubble would otherwise float on top of the fanned leaves

  const events = stack._events || [];
  const overflowing = events.length > STACK_MAX_FAN;
  // If overflowing, reserve the last fan slot for the "+N" badge.
  const visibleCount = overflowing ? STACK_MAX_FAN - 1 : events.length;
  const visible = events.slice(-visibleCount);          // most recent N
  const hidden  = events.slice(0, events.length - visibleCount);
  const totalSlots = visibleCount + (overflowing ? 1 : 0);

  for (let i = 0; i < visibleCount; i++) {
    const e = visible[i];
    const angle = (i / totalSlots) * 2 * Math.PI - Math.PI / 2;  // start at top
    const rx = Math.cos(angle) * STACK_FAN_RADIUS;
    const ry = Math.sin(angle) * STACK_FAN_RADIUS;

    const leaf = document.createElement('div');
    leaf.className = 'event-dot stack-leaf';
    leaf.dataset.eventId = e.id;
    if (e.id === state.selectedEventId) leaf.classList.add('selected');
    leaf.style.setProperty('--rx', rx + 'px');
    leaf.style.setProperty('--ry', ry + 'px');
    const icon = e.tag && TAG_ICONS[e.tag];
    if (icon) {
      leaf.classList.add('has-tag');
      leaf.dataset.tag = e.tag;
      leaf.textContent = icon;
    }
    // Hover tooltip — title (heading / bold-only line) or snippet fallback.
    // Truncated so a very long snippet doesn't stretch the bubble off-screen.
    const tipText = extractTitle(e) || cleanInlineText(e.snippet || '') ||
                    `${formatEventDate(e.date)}${e.tag ? ' · ' + e.tag : ''}`;
    leaf._tooltipText = tipText.length > 80 ? tipText.slice(0, 79).trimEnd() + '…' : tipText;
    leaf.addEventListener('mouseenter', () => showPinTooltip(leaf));
    leaf.addEventListener('mouseleave', () => hidePinTooltip());
    leaf.addEventListener('click', (ev) => {
      ev.stopPropagation();
      showEvent(e);
      collapseStack(stack);  // close the fan once a leaf is chosen
    });
    stack.appendChild(leaf);
  }

  if (overflowing) {
    const i = visibleCount;
    const angle = (i / totalSlots) * 2 * Math.PI - Math.PI / 2;
    const rx = Math.cos(angle) * STACK_FAN_RADIUS;
    const ry = Math.sin(angle) * STACK_FAN_RADIUS;

    const more = document.createElement('div');
    more.className = 'stack-overflow';
    more.style.setProperty('--rx', rx + 'px');
    more.style.setProperty('--ry', ry + 'px');
    more.textContent = `+${hidden.length}`;
    more.title = `${hidden.length} more event(s) at this location — click for list`;
    more.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openStackOverflowList(stack, hidden);
    });
    stack.appendChild(more);
  }

  stack.classList.add('expanded');
}

function collapseStack(stack) {
  stack.querySelectorAll('.stack-leaf, .stack-overflow, .stack-overflow-list').forEach(el => el.remove());
  stack.classList.remove('expanded');
}

function openStackOverflowList(stack, hiddenEvents) {
  // Toggle: a second click on "+N" while the popover is open closes it.
  const existing = stack.querySelector('.stack-overflow-list');
  if (existing) {
    existing.remove();
    return;
  }

  const list = document.createElement('div');
  list.className = 'stack-overflow-list';
  // Most recent first within the hidden list.
  for (const e of [...hiddenEvents].reverse()) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'stack-overflow-row';
    const icon = e.tag && TAG_ICONS[e.tag] || '•';
    const snippet = cleanInlineText(e.snippet || '').slice(0, 50);
    row.innerHTML = '';
    row.append(
      Object.assign(document.createElement('span'), { className: 'sov-icon', textContent: icon }),
      Object.assign(document.createElement('span'), { className: 'sov-date', textContent: formatEventDate(e.date) }),
      Object.assign(document.createElement('span'), { className: 'sov-snippet', textContent: snippet }),
    );
    row.addEventListener('click', (ev) => {
      ev.stopPropagation();
      showEvent(e);
      collapseStack(stack);
    });
    list.appendChild(row);
  }
  stack.appendChild(list);
}

function updateDotCapNote(cappedTotal) {
  const labels = document.getElementById('timeline-labels');
  if (!labels) return;
  let note = labels.querySelector('.cap-note');
  if (cappedTotal > 0) {
    if (!note) {
      note = document.createElement('span');
      note.className = 'cap-note';
      // Insert between the two date labels.
      const first = labels.firstElementChild;
      if (first) labels.insertBefore(note, first.nextSibling);
      else labels.appendChild(note);
    }
    note.textContent = `map: ${MAX_DOTS_UNFILTERED} of ${cappedTotal} most recent — pick a session to see older`;
  } else if (note) {
    note.remove();
  }
}

function updateDotPositions() {
  const { x: vx, y: vy, scale: vs } = state.view;
  const dots = document.getElementById('event-dots');
  if (!dots) return;
  for (const dot of dots.children) {
    const mx = parseFloat(dot.dataset.mx);
    const my = parseFloat(dot.dataset.my);
    if (Number.isFinite(mx) && Number.isFinite(my)) {
      dot.style.left = (vx + mx * vs) + 'px';
      dot.style.top = (vy + my * vs) + 'px';
    }
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
    m.title = `${formatEventDate(e.date)} — ${cleanInlineText(e.snippet || '')}`;
    container.appendChild(m);
  }

  // The snapshot that's active at the start of the current range (latest one
  // with date <= tMin). We still render it even though its date predates the
  // range — pinned to the left edge — because it IS the map shown for events
  // at the start of the range. Without this, sessions whose first map was
  // taken just before they began would have no left-edge tick at all (e.g.
  // darthsunday's 1337-04-01 map vs Session 1 starting 1337-04-02).
  let activeAtStart = null;
  for (const s of state.snapshots) {
    const sT = parseDate(s.date);
    if (sT <= state.tMin && (!activeAtStart || parseDate(activeAtStart.date) < sT)) {
      activeAtStart = s;
    }
  }

  for (const s of state.snapshots) {
    const sT = parseDate(s.date);
    if (sT > state.tMax) continue;
    // Skip past snapshots that have been superseded by activeAtStart — only
    // the one actually showing at range start gets the pinned-left treatment.
    if (sT < state.tMin && s !== activeAtStart) continue;
    const pct = sT < state.tMin ? 0 : ((sT - state.tMin) / range) * 100;
    const m = document.createElement('div');
    m.className = 'tl-mark snapshot';
    m.style.left = `${pct}%`;
    m.title = `${formatEventDate(s.date)}${s.label ? ' — ' + s.label : ''}`;
    // Year stamp shown above the blue tick by the .tl-mark.snapshot::before
    // rule in style.css. Prefer the campaign-defined label (usually a year
    // like "1337"); fall back to the year extracted from the ISO date so the
    // stamp is never empty.
    m.dataset.label = s.label || s.date.slice(0, 4);
    m.addEventListener('click', () => {
      // Clamp to tMin so clicking the pinned-left tick of a pre-range snapshot
      // seeks to the start of the active window instead of outside it.
      state.currentTime = Math.max(state.tMin, sT);
      document.getElementById('timeline').value = timeToSlider(state.currentTime);
      render();
      updateBrowserVisibility();
    });
    container.appendChild(m);
  }
}

// Apply / clear the `.selected` class on whichever map pin represents the
// currently-selected event. Called by showEvent and re-applied after every
// renderDots pass so the highlight survives timeline scrubs and filter changes.
function updateMapSelection() {
  const dotsEl = document.getElementById('event-dots');
  if (!dotsEl) return;
  for (const el of dotsEl.querySelectorAll('.selected')) el.classList.remove('selected');
  const id = state.selectedEventId;
  if (!id) return;
  for (const dot of dotsEl.querySelectorAll('.event-dot[data-event-id]')) {
    if (dot.dataset.eventId === id) dot.classList.add('selected');
  }
  for (const stack of dotsEl.querySelectorAll('.event-stack')) {
    const ids = (stack.dataset.eventIds || '').split(',');
    if (ids.includes(id)) stack.classList.add('selected');
  }
}

function showEvent(e) {
  state.selectedEventId = e.id;
  updateMapSelection();
  const panel = document.getElementById('event-panel');
  const place = [countryDisplay(e.country, e.countryRaw), e.province].filter(Boolean).join(' / ');
  // Clear panel content but preserve the injected .panel-toggle button.
  for (const child of [...panel.children]) {
    if (!child.classList.contains('panel-toggle')) child.remove();
  }

  const header = document.createElement('div');
  header.className = 'event-header';
  const h = document.createElement('h2');
  h.textContent = formatEventDate(e.date);
  header.appendChild(h);

  // Action buttons live in a flex column on the right side of the header so
  // multiple actions (Zoom to pin + View on Discord) stack neatly without
  // crowding the date title.
  const actions = document.createElement('div');
  actions.className = 'event-actions';
  const coords = resolveCoords(e);
  if (coords) {
    const btn = document.createElement('button');
    btn.className = 'jump-to-pin';
    btn.textContent = 'Zoom to pin';
    btn.title = 'Pan and zoom the map to this event';
    btn.addEventListener('click', () => zoomToCoords(coords));
    actions.appendChild(btn);
  }
  // "View on Discord" — only if we know the guild (from campaigns.json) and
  // this event has its channel_id stored (set on every event from now on; for
  // legacy events we backfilled from the event_meta cache where possible).
  const sync = state.discordSync;
  if (sync && sync.guild_id && e.channel_id && e.id) {
    const link = document.createElement('a');
    link.className = 'view-on-discord';
    link.href = `https://discord.com/channels/${sync.guild_id}/${e.channel_id}/${e.id}`;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'View on Discord';
    link.title = 'Open the original Discord post in a new tab';
    actions.appendChild(link);
  }
  if (actions.childNodes.length > 0) header.appendChild(actions);
  panel.appendChild(header);

  const meta = document.createElement('p');
  meta.className = 'meta';
  meta.textContent = [place, e.author].filter(Boolean).join(' — ');
  panel.appendChild(meta);

  if (e.tag) {
    const tagEl = document.createElement('p');
    tagEl.className = 'event-tag';
    const icon = TAG_ICONS[e.tag] || '';
    tagEl.textContent = `${icon} ${e.tag}`.trim();
    panel.appendChild(tagEl);
  }

  const body = document.createElement('div');
  body.className = 'body';
  body.appendChild(renderMarkdown(e.fullText || e.snippet || ''));
  panel.appendChild(body);

  if (e.images && e.images.length) {
    const wrap = document.createElement('div');
    wrap.className = 'event-images';
    for (const img of e.images) {
      const a = document.createElement('a');
      a.href = img.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      const imgEl = document.createElement('img');
      imgEl.src = img.url;
      imgEl.alt = img.filename || '';
      imgEl.loading = 'lazy';
      imgEl.referrerPolicy = 'no-referrer';
      imgEl.addEventListener('error', () => a.classList.add('broken'));
      a.appendChild(imgEl);
      wrap.appendChild(a);
    }
    panel.appendChild(wrap);
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

// Discord custom emoji syntax: <:name:id> or <a:name:id> (animated). The CDN
// asset would need the original ID to render, and the syntax otherwise shows
// as literal text — so we strip them everywhere they'd appear.
const CUSTOM_EMOJI_RE = /<a?:[a-zA-Z0-9_]+:\d+>/g;
// User / role / nickname mentions: <@123>, <@!123>, <@&123>. We have no
// airgapped way to map the snowflake to a display name (events.json stores
// authors by username, not by id), so we drop these entirely.
const MENTION_RE = /<@[!&]?\d+>/g;
// Spoiler tokens: ||hidden text||. Restricted to a single line so a stray ||
// can't consume the rest of a post.
const SPOILER_RE = /\|\|([^\n]+?)\|\|/g;

function stripCustomEmoji(text) {
  return text ? text.replace(CUSTOM_EMOJI_RE, '') : text;
}

function stripMentions(text) {
  return text ? text.replace(MENTION_RE, '') : text;
}

// Discord-style masked links: [Title](URL) — and the nested form players
// sometimes paste, [Title]([alias](URL)). We keep only the visible title.
// The regex only matches links with no parens in the URL portion, then loops
// — that strips innermost first, so nested forms collapse in subsequent
// iterations.
function stripLinks(text) {
  if (!text) return text;
  let prev;
  do {
    prev = text;
    text = text.replace(/\[([^\]\n]+)\]\(([^()\n]*)\)/g, '$1');
  } while (text !== prev);
  return text;
}

// Snippet/inline plain-text cleaner: drop custom emojis, user mentions, masked
// links, subtext markers, AND markdown bold / italic markers (they're shown as
// raw asterisks in tooltips + the events table column). Spoilers are replaced
// with a "[spoiler]" placeholder so previews don't leak hidden content. The
// body renderer keeps spoiler / bold / italic markers — renderInline turns
// them into the right DOM. Bold pattern runs first so ** isn't half-eaten
// by the * pass.
function cleanInlineText(text) {
  if (!text) return text;
  let s = stripCustomEmoji(text);
  s = stripMentions(s);
  s = stripLinks(s);
  s = s.replace(SPOILER_RE, '[spoiler]');
  // Subtext: drop the "-# " marker, keep the text (no way to render smaller
  // in a plain-text preview).
  s = s.replace(/^\s*-#\s+/gm, '');
  s = s.replace(/\*\*([^*\n]+?)\*\*/g, '$1');
  s = s.replace(/\*([^*\n]+?)\*/g, '$1');
  return s.trim();
}

// Title cleaner: cleanInlineText + drop a single trailing colon
// ("An Age of War:" → "An Age of War"). One colon only — "x::" is preserved
// since it's almost always intentional emphasis.
function cleanTitleText(text) {
  let s = cleanInlineText(text);
  if (s && s.endsWith(':')) s = s.slice(0, -1).trimEnd();
  return s;
}

function renderInline(text, parent) {
  // Inline tokens: **bold**, *italic*, ||spoiler||. Tokens are alternating
  // non-marker / marker chunks. Spoiler content is rendered recursively so
  // nested **bold** inside ||...|| renders correctly when revealed.
  const re = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*|\|\|[^\n]+?\|\|)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
    const tok = m[0];
    if (tok.startsWith('**')) {
      const b = document.createElement('strong');
      b.textContent = tok.slice(2, -2);
      parent.appendChild(b);
    } else if (tok.startsWith('||')) {
      const s = document.createElement('span');
      s.className = 'spoiler';
      // Recurse so the spoiler can still contain inline bold/italic.
      renderInline(tok.slice(2, -2), s);
      parent.appendChild(s);
    } else {
      const i = document.createElement('em');
      i.textContent = tok.slice(1, -1);
      parent.appendChild(i);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
}

function renderMarkdown(text) {
  const frag = document.createDocumentFragment();
  if (!text) return frag;
  // Pre-strip things that should never reach the renderer:
  //   - Discord custom emojis (no client-side CDN access)
  //   - Mentions (no airgapped name resolution)
  //   - Masked links (display the visible title, drop the URL)
  text = stripCustomEmoji(text);
  text = stripMentions(text);
  text = stripLinks(text);
  // Force any heading-prefixed line (# / ## / ### followed by a space) to
  // start its own block, even if the player didn't leave a blank line before
  // it. Without this, a post like "# Title\n### Subhead\nbody" renders the
  // `### Subhead` line as inline text inside the title's paragraph instead of
  // as its own subheading. The gm flag lets `^` match every line start.
  text = text.replace(/^(#{1,3}\s)/gm, '\n\n$1');
  // Paragraph break on 2+ newlines.
  for (const block of text.split(/\n{2,}/)) {
    const lines = block.split('\n');
    if (lines.length === 1) {
      const line = lines[0];
      const h = line.match(/^(#{1,3})\s+(.+)$/);
      if (h) {
        const el = document.createElement('h' + (h[1].length + 2)); // # → h3, ## → h4, ### → h5
        renderInline(h[2], el);
        frag.appendChild(el);
        continue;
      }
      const sub = line.match(/^\s*-#\s+(.+)$/);
      if (sub) {
        const p = document.createElement('p');
        p.className = 'subtext';
        renderInline(sub[1], p);
        frag.appendChild(p);
        continue;
      }
    }
    const p = document.createElement('p');
    lines.forEach((line, i) => {
      const h = line.match(/^(#{1,3})\s+(.+)$/);
      if (h && i === 0) {
        // Heading at top of multi-line block: render heading then rest as paragraph.
        const el = document.createElement('h' + (h[1].length + 2));
        renderInline(h[2], el);
        frag.appendChild(el);
        return;
      }
      if (i > 0) p.appendChild(document.createElement('br'));
      const sub = line.match(/^\s*-#\s+(.+)$/);
      if (sub) {
        // Subtext line inside a multi-line paragraph — inline <small> so it
        // mixes naturally with surrounding text in the same <p>.
        const small = document.createElement('small');
        small.className = 'subtext';
        renderInline(sub[1], small);
        p.appendChild(small);
      } else {
        renderInline(line, p);
      }
    });
    if (p.childNodes.length > 0) frag.appendChild(p);
  }
  return frag;
}

// Strip surrounding Discord spoiler markers from a single line. Handles
// both balanced `||...||` and an unclosed leading `||` (Discord renders the
// rest of the line as a spoiler until a newline). Returns the inner text;
// pure pass-through if no markers.
function stripSpoilerWrap(line) {
  let s = line;
  const lead = s.match(/^(\s*)\|\|(.*)$/);
  if (lead) {
    s = lead[1] + lead[2];
    const trail = s.match(/^(.*)\|\|(\s*)$/);
    if (trail) s = trail[1] + trail[2];
  }
  return s;
}

function extractTitle(e) {
  let raw = null;
  if (e.title) {
    raw = e.title;
  } else if (e.fullText) {
    for (const line of e.fullText.split('\n')) {
      // Try the line as-is first; if neither pattern matches, try again
      // after unwrapping `||...||` markers. Players sometimes wrap a whole
      // headline in spoiler markers (e.g. `||### A letter from France||`)
      // to hide a story beat — we should still recognise it as a title.
      const candidates = [line];
      const unwrapped = stripSpoilerWrap(line);
      if (unwrapped !== line) candidates.push(unwrapped);
      for (const cand of candidates) {
        let m = cand.match(/^\s*#{1,3}\s+(.+?)\s*$/);
        if (m) { raw = m[1]; break; }
        m = cand.match(/^\s*\*{2,3}\s*([^*]+?)\s*\*{2,3}\s*$/);
        if (m) { raw = m[1].trim(); break; }
      }
      if (raw) break;
    }
  }
  // cleanTitleText handles custom emojis, residual markdown markers, and
  // a single trailing colon ("An Age of War:" → "An Age of War").
  const cleaned = cleanTitleText(raw);
  return cleaned || null;
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
    tdDate.textContent = formatEventDate(e.date);
    tr.appendChild(tdDate);

    const tdTag = document.createElement('td');
    tdTag.className = 'col-tag' + (e.tag ? '' : ' muted');
    if (e.tag) {
      const icon = TAG_ICONS[e.tag] || '';
      tdTag.textContent = `${icon} ${e.tag}`.trim();
    } else {
      tdTag.textContent = '—';
    }
    tr.appendChild(tdTag);

    const tdCountry = document.createElement('td');
    tdCountry.className = 'col-country' + (e.country ? '' : ' muted');
    tdCountry.textContent = countryDisplay(e.country, e.countryRaw) || '—';
    if (e.country) tdCountry.title = e.country;
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
    tdSnip.textContent = extractTitle(e) || cleanInlineText(e.snippet || '');
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

// Click on a spoiler → reveal it permanently (across the rest of the
// session). Hover-grace still applies for quick peeks. Delegated on body so
// it works for spoilers rendered later (e.g. when a new event is opened in
// the side panel). The `.revealed` class is added; CSS keeps revealed
// spoilers visible regardless of hover state.
document.addEventListener('click', (ev) => {
  const sp = ev.target.closest('.spoiler');
  if (sp) sp.classList.add('revealed');
});

// Inject a small button into each collapsible panel and wire toggle behaviour.
// Persists state across reloads via localStorage so e.g. opening calibrate then
// returning to the viewer doesn't reset preferred layout.
function wirePanelToggles() {
  function makeToggle(panel, key, expandedGlyph, collapsedGlyph, expandedTitle, collapsedTitle) {
    const btn = document.createElement('button');
    btn.className = 'panel-toggle';
    btn.type = 'button';
    panel.appendChild(btn);

    function apply(collapsed) {
      panel.classList.toggle('collapsed', collapsed);
      btn.textContent = collapsed ? collapsedGlyph : expandedGlyph;
      btn.title = collapsed ? collapsedTitle : expandedTitle;
      btn.setAttribute('aria-expanded', String(!collapsed));
    }

    const initial = localStorage.getItem(key) === '1';
    apply(initial);

    btn.addEventListener('click', () => {
      const nowCollapsed = !panel.classList.contains('collapsed');
      localStorage.setItem(key, nowCollapsed ? '1' : '0');
      apply(nowCollapsed);
    });
    return btn;
  }

  // Right event panel — button lives inside the panel itself (top-right when
  // expanded, vertically centred in the rail when collapsed — CSS handles both).
  const eventPanel = document.getElementById('event-panel');
  if (eventPanel) {
    makeToggle(eventPanel, 'dmv:eventPanelCollapsed',
               '›', '‹',
               'Hide event panel', 'Show event panel');
  }

  // Bottom browser — button lives in the .tabs strip so it stays visible
  // even when the tab-panel content is hidden.
  const browser = document.getElementById('browser');
  if (browser) {
    const tabs = browser.querySelector('.tabs');
    if (tabs) {
      const btn = document.createElement('button');
      btn.className = 'panel-toggle';
      btn.type = 'button';
      tabs.appendChild(btn);

      function applyBrowser(collapsed) {
        browser.classList.toggle('collapsed', collapsed);
        btn.textContent = collapsed ? '▴' : '▾';
        btn.title = collapsed ? 'Show events list' : 'Hide events list';
        btn.setAttribute('aria-expanded', String(!collapsed));
      }
      applyBrowser(localStorage.getItem('dmv:browserCollapsed') === '1');
      btn.addEventListener('click', () => {
        const nowCollapsed = !browser.classList.contains('collapsed');
        localStorage.setItem('dmv:browserCollapsed', nowCollapsed ? '1' : '0');
        applyBrowser(nowCollapsed);
      });
    }
  }
}

async function init() {
  try {
    const game = window.CAMPAIGN_GAME || 'eu4';
    const [eventsData, snapshotsData, coordsData, provincesData, sessionsData, tagsData, rawCountriesText, manifestData, eventTagsData] = await Promise.all([
      loadJSON('data/events.json'),
      loadJSON('data/snapshots.json'),
      loadJSON('data/coords.json'),
      loadJSON(`../assets/reference/${game}/provinces.json`).catch(() => ({})),
      loadJSON('data/sessions.json').catch(() => ({ sessions: [] })),
      loadJSON(`../assets/reference/${game}/tags.json`).catch(() => ({})),
      fetch(`../assets/reference/${game}/00_countries.txt`).then(r => r.ok ? r.text() : '').catch(() => ''),
      loadJSON('../campaigns.json').catch(() => null),
      loadJSON('../assets/event-tags.json').catch(() => null),
    ]);

    // Populate TAG_ICONS from the shared event-tags.json registry.
    if (eventTagsData && eventTagsData.tags) {
      for (const [canon, info] of Object.entries(eventTagsData.tags)) {
        if (info && info.icon) TAG_ICONS[canon] = info.icon;
      }
    }

    // Find this campaign's entry in the root manifest (matched by folder name
    // derived from the URL — e.g. /darthsunday/view.html → "darthsunday").
    // Used to surface the discord_sync block (guild_id) so showEvent can
    // build a `https://discord.com/channels/.../...` link to the original
    // Discord post. Falls through silently for non-sync'd campaigns.
    state.discordSync = null;
    if (manifestData && Array.isArray(manifestData.campaigns)) {
      const segments = location.pathname.split('/').filter(Boolean);
      const folder = segments.length >= 2 ? segments[segments.length - 2] : null;
      if (folder) {
        const entry = manifestData.campaigns.find(c => c.folder === folder);
        if (entry && entry.discord_sync) state.discordSync = entry.discord_sync;
      }
    }

    // Build tag → display name map. tags.json first (curated/canonical),
    // then 00_countries.txt fills in anything missing (EU4 — derive name from filename).
    state.countryNames = {};
    for (const [tag, info] of Object.entries(tagsData || {})) {
      if (info && info.name) state.countryNames[tag] = info.name;
    }
    if (rawCountriesText) {
      const re = /^([A-Z][A-Z0-9]{1,3})\s*=\s*"countries\/([^"]+)\.txt"/gm;
      let m;
      while ((m = re.exec(rawCountriesText)) !== null) {
        if (!state.countryNames[m[1]]) {
          state.countryNames[m[1]] = m[2].replace(/([a-z])([A-Z])/g, '$1 $2');
        }
      }
    }

    const rawEvents = eventsData.events || [];
    const dropped = rawEvents.filter(e => !Number.isFinite(parseDate(e.date)));
    if (dropped.length > 0) {
      console.warn(`Dropping ${dropped.length} event(s) with unparseable dates:`,
                   dropped.map(e => ({ id: e.id, date: e.date })));
    }
    state.events = rawEvents
      .filter(e => Number.isFinite(parseDate(e.date)))
      .slice()
      .sort((a, b) => parseDate(a.date) - parseDate(b.date));
    state.snapshots = (snapshotsData.snapshots || []).slice().sort(
      (a, b) => parseDate(a.date) - parseDate(b.date)
    );
    if (snapshotsData.config) Object.assign(state.mapConfig, snapshotsData.config);
    state.coords = { countries: {}, provinces: {}, ...coordsData };
    state.provinces = provincesData;
    state.sessions = sessionsData.sessions || [];

    // Unified case-insensitive index. positions-data first (canonical key + any aliases),
    // then manual coords.json overrides.
    state.provincesIndex = {};
    for (const [n, info] of Object.entries(provincesData)) {
      if (info && Array.isArray(info.coords)) {
        state.provincesIndex[n.toLowerCase()] = info.coords;
        for (const a of (info.aliases || [])) {
          if (!state.provincesIndex[a.toLowerCase()]) {
            state.provincesIndex[a.toLowerCase()] = info.coords;
          }
        }
      }
    }
    for (const [n, coords] of Object.entries(state.coords.provinces || {})) {
      if (Array.isArray(coords)) state.provincesIndex[n.toLowerCase()] = coords;
    }

    // Per-game column label for the events table.
    const provHeader = document.querySelector('th.col-province');
    if (provHeader) {
      const labels = { eu4: 'Province', eu5: 'Location' };
      provHeader.textContent = labels[window.CAMPAIGN_GAME] || 'Location';
    }

    const allTimes = [
      ...state.events.map(e => parseDate(e.date)),
      ...state.snapshots.map(s => parseDate(s.date)),
    ];
    if (allTimes.length > 0) {
      state.allTMin = Math.min(...allTimes);
      state.allTMax = Math.max(...allTimes);
    }

    // Default the filter to the most recent session (last entry after the
    // start-date sort that sessions.html enforces). Auto-adapts as new
    // sessions are added so the page always opens on the current one.
    // Sessions also clamp the timeline range, so this has to land BEFORE the
    // tMin/tMax / currentTime block below.
    if (state.sessions.length > 0) {
      state.filter = `session:${state.sessions.length - 1}`;
    }

    // Resolve the active timeline range from whatever filter is currently
    // active — session bounds when a session is selected, otherwise the full
    // event+snapshot span.
    const activeSession = getActiveSession();
    if (activeSession) {
      state.tMin = parseDate(activeSession.start);
      state.tMax = sessionEndTime(activeSession);
    } else {
      state.tMin = state.allTMin;
      state.tMax = state.allTMax;
    }

    // Default the timeline cursor to the most recently uploaded map snapshot,
    // clamped to the active range. snapshots are sorted ascending by date in
    // loadJSON above, so the last entry is the latest. Falls back to tMin if
    // there are no snapshots at all (events-only campaign).
    if (state.snapshots.length > 0) {
      state.currentTime = parseDate(state.snapshots[state.snapshots.length - 1].date);
    } else {
      state.currentTime = state.tMin;
    }
    state.currentTime = Math.min(state.tMax, Math.max(state.tMin, state.currentTime));

    const slider = document.getElementById('timeline');
    slider.min = 0;
    slider.max = SLIDER_RES;
    slider.step = 1;
    slider.value = timeToSlider(state.currentTime);
    slider.addEventListener('input', () => {
      state.currentTime = sliderToTime(parseInt(slider.value, 10));
      scheduleRender();
    });

    // Persisted resolution toggle, only meaningful when any snapshot has a
    // lowres variant. Storage key is versioned: when we change the first-
    // visit default (here, from 'full' to 'lowres'), bump the suffix so prior
    // users' cached choice doesn't override the new default. They'll get the
    // new default once, and any subsequent toggle persists under the new key.
    const RESOLUTION_STORAGE_KEY = 'mapResolution_v2';
    const savedRes = localStorage.getItem(RESOLUTION_STORAGE_KEY);
    if (savedRes === 'full' || savedRes === 'lowres') state.resolution = savedRes;
    const resWrap = document.getElementById('resolution-toggle-wrap');
    const resEl = document.getElementById('resolution-toggle');
    const hasLowres = state.snapshots.some(s => s.image_lowres);
    if (resWrap && hasLowres) {
      resWrap.hidden = false;
      resEl.value = state.resolution;
      resEl.addEventListener('change', () => {
        state.resolution = resEl.value;
        localStorage.setItem(RESOLUTION_STORAGE_KEY, state.resolution);
        preloadSnapshots();
        render();
      });
    }

    const filterEl = document.getElementById('filter');
    if (state.sessions.length > 0) {
      const og = document.createElement('optgroup');
      og.label = 'Sessions';
      state.sessions.forEach((s, idx) => {
        const opt = document.createElement('option');
        opt.value = `session:${idx}`;
        const startYear = (s.start || '').split('-')[0];
        const endYear = (s.end || '').split('-')[0];
        // Open-ended sessions (no end date set) get "(YYYY–present)" so the
        // dropdown communicates that the session is still in progress.
        let yrs = '';
        if (startYear && endYear) {
          yrs = ` (${startYear}–${endYear})`;
        } else if (startYear && isSessionOpenEnded(s)) {
          yrs = ` (${startYear}–present)`;
        }
        // Editor saves `name`; sessions added by hand sometimes use `label`.
        // Accept either so neither schema fights the other.
        const label = s.name || s.label || `Session ${idx + 1}`;
        opt.textContent = `${label}${yrs}`;
        og.appendChild(opt);
      });
      filterEl.appendChild(og);
    }
    filterEl.value = state.filter;
    filterEl.addEventListener('change', () => applyFilter(filterEl.value));

    const labels = document.getElementById('timeline-labels');
    if (state.tMax > state.tMin) {
      // Match applyFilter's "(ongoing)" decoration so the initial render of
      // an open-ended session doesn't look like it has a hard end date.
      const openTag = activeSession && isSessionOpenEnded(activeSession)
        ? ' <em>(ongoing)</em>' : '';
      labels.innerHTML =
        `<span>${formatDate(state.tMin)}</span>` +
        `<span>${formatDate(state.tMax)}${openTag}</span>`;
    }

    renderTimelineMarks();
    renderBrowser();
    wireTabs();
    wirePanelToggles();
    wireMapInteractions();
    preloadSnapshots();
    render();
  } catch (err) {
    document.getElementById('event-panel').innerHTML =
      `<p class="empty">Could not load data: ${err.message}</p>`;
    console.error(err);
  }
}

init();
