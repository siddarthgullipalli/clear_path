/* ShipGuard — dashboard + routes + OpenUI stream */
'use strict';

const USE_MOCK_JS  = true;
const STREAM_PORT  = 8090;
const STREAM_URL   = `http://localhost:${STREAM_PORT}/api/stream-risk`;

const SEVERITY_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

let ACTIVE_VIEW  = 'dashboard'; // 'dashboard' | 'routes' | 'stream'

// ── In-memory route registry ──────────────────────────────────────────────────
// Source of truth for all active shipments. Mutated by the Routes page.
// Analysis reads from this so deleted routes are excluded.

let SHIPMENTS = [
  { shipment_id: 'SH-01', vessel: 'MV Pacific Star',    origin: 'Taipei',    destination: 'Rotterdam',    cargo: 'Semiconductors',   status: 'DIVERTED',   eta_days: 22 },
  { shipment_id: 'SH-04', vessel: 'MV Coral Queen',     origin: 'Sydney',    destination: 'Dubai',        cargo: 'Minerals',         status: 'DELAYED',    eta_days: 16 },
  { shipment_id: 'SH-02', vessel: 'MV Asian Horizon',   origin: 'Singapore', destination: 'Los Angeles',  cargo: 'Electronics',      status: 'DELAYED',    eta_days: 18 },
  { shipment_id: 'SH-03', vessel: 'MV Northern Light',  origin: 'Shanghai',  destination: 'Hamburg',      cargo: 'Automotive Parts', status: 'IN_TRANSIT', eta_days: 25 },
  { shipment_id: 'SH-05', vessel: 'MV Atlantic Bridge', origin: 'New York',  destination: 'Lagos',        cargo: 'Machinery',        status: 'IN_TRANSIT', eta_days: 14 },
];

let nextShipmentNum = 6;  // next SH-XX counter
let analysisHasRun  = false;

// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(message, delayMs) {
  if (delayMs === undefined) delayMs = 300;
  const toast = document.getElementById('slackToast');
  const msgEl = document.getElementById('toastMessage');
  if (msgEl) msgEl.textContent = message;
  clearTimeout(toast._showTimer);
  clearTimeout(toast._hideTimer);
  toast._showTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      toast.classList.add('visible');
      toast._hideTimer = setTimeout(() => toast.classList.remove('visible'), 4000);
    });
  }, delayMs);
}

function showSlackToast() {
  showToast('Alert sent to #logistics-channel', 500);
}

// ── Mode indicator ────────────────────────────────────────────────────────────

function initModeIndicator() {
  const dot   = document.getElementById('modeDot');
  const label = document.getElementById('modeLabel');
  if (USE_MOCK_JS) {
    dot.className     = 'mode-dot demo';
    label.textContent = 'Demo mode';
  } else {
    dot.className     = 'mode-dot live';
    label.textContent = 'Live';
  }
}

// ── Header subtitle ───────────────────────────────────────────────────────────

function updateSubtitle(risks) {
  analysisHasRun = true;
  const alerts   = risks.filter(r => r.severity === 'CRITICAL' || r.severity === 'HIGH').length;
  const advisory = risks.filter(r => r.severity === 'MEDIUM').length;
  const clear    = risks.filter(r => r.severity === 'LOW').length;
  const parts    = [];
  if (alerts   > 0) parts.push(`${alerts} ${alerts === 1 ? 'alert' : 'alerts'} active`);
  if (advisory > 0) parts.push(`${advisory} ${advisory === 1 ? 'advisory' : 'advisories'}`);
  if (clear    > 0) parts.push(`${clear} clear`);
  document.getElementById('headerSubtitle').textContent =
    parts.length > 0 ? parts.join(' · ') : '0 issues detected';
}

function refreshRouteCount() {
  if (!analysisHasRun) {
    const n = SHIPMENTS.length;
    document.getElementById('headerSubtitle').textContent =
      `Supply chain risk intelligence — ${n} ${n === 1 ? 'shipment' : 'shipments'} active`;
  }
}

// ── Status counts bar ─────────────────────────────────────────────────────────

function updateStatusCounts(risks) {
  const el  = document.getElementById('statusCounts');
  const map = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
  risks.forEach(r => { if (map[r.severity] !== undefined) map[r.severity]++; });

  const colors = { CRITICAL: '#f85149', HIGH: '#fb8500', MEDIUM: '#d29922', LOW: '#3fb950' };
  el.innerHTML = Object.entries(map).map(([sev, count]) => `
    <span class="status-count">
      <span class="status-count-dot" style="background:${colors[sev]}"></span>
      <span>${count} ${sev.charAt(0) + sev.slice(1).toLowerCase()}</span>
    </span>`).join('');
}

// ── Skeleton loading ──────────────────────────────────────────────────────────

function showSkeletons(gridEl) {
  const count = Math.max(SHIPMENTS.length, 1);
  const one = `
    <div class="skeleton-card">
      <div style="display:flex;justify-content:space-between;gap:12px">
        <div class="skeleton-block" style="height:18px;width:55%"></div>
        <div class="skeleton-block" style="height:18px;width:18%"></div>
      </div>
      <div class="skeleton-block" style="height:12px;width:38%"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
        <div class="skeleton-block" style="height:68px;border-radius:5px"></div>
        <div class="skeleton-block" style="height:68px;border-radius:5px"></div>
        <div class="skeleton-block" style="height:68px;border-radius:5px"></div>
        <div class="skeleton-block" style="height:68px;border-radius:5px"></div>
      </div>
      <div class="skeleton-block" style="height:44px"></div>
    </div>`;
  gridEl.innerHTML = Array(count).fill(one).join('');
}

// ── Risk data fetch ───────────────────────────────────────────────────────────

async function fetchRisks() {
  if (USE_MOCK_JS) {
    const activeIds   = new Set(SHIPMENTS.map(s => s.shipment_id));
    const res         = await fetch('/mocks/llm_decision.json');
    if (!res.ok) throw new Error(`Mock fetch failed: ${res.status}`);
    const mockResults = await res.json();

    // Filter to currently active shipments only
    const filtered = mockResults.filter(r => activeIds.has(r.shipment_id));

    // Add placeholder results for new routes not in mock data
    const mockIds = new Set(mockResults.map(r => r.shipment_id));
    SHIPMENTS.forEach(s => {
      if (!mockIds.has(s.shipment_id)) {
        filtered.push({
          shipment_id:      s.shipment_id,
          vessel:           s.vessel,
          origin:           s.origin,
          destination:      s.destination,
          cargo:            s.cargo,
          status:           'IN_TRANSIT',
          severity:         'LOW',
          reasoning:        'New route added — weather analysis pending next backend run.',
          alternate_route:  null,
          eta_impact_hrs:   0,
          weather_snapshot: {
            wind_knots_max_72h: 0,
            storm_probability:  0,
            wave_height_m:      0,
            worst_waypoint:     null,
          },
        });
      }
    });

    return filtered;
  }

  const res = await fetch('/api/analyze');
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ── Card helpers ──────────────────────────────────────────────────────────────

function formatWaypoint(ws, sev) {
  if (sev !== 'CRITICAL' && sev !== 'HIGH') return '';
  const wp = ws && ws.worst_waypoint;
  if (!Array.isArray(wp) || wp.length < 2) return '';
  const lat    = wp[0], lon = wp[1];
  const latStr = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'}`;
  const lonStr = `${Math.abs(lon).toFixed(1)}°${lon >= 0 ? 'E' : 'W'}`;
  const color  = sev === 'CRITICAL' ? '#f85149' : '#d29922';
  return `<p class="waypoint-callout" style="color:${color}">⚠ Risk peak at ${latStr}, ${lonStr}</p>`;
}

function buildCard(risk) {
  const sev    = risk.severity || 'LOW';
  const status = risk.status   || 'IN_TRANSIT';
  const ws     = risk.weather_snapshot || {};

  const wind     = ws.wind_knots_max_72h  || 0;
  const storm    = ws.storm_probability   || 0;
  const stormPct = (storm <= 1 ? storm * 100 : storm).toFixed(0);
  const wave     = (ws.wave_height_m || 0).toFixed(1);

  const warnCls  = wind >= 75 ? 'danger' : wind >= 60 ? 'warn' : '';
  const stormCls = storm * 100 >= 80 ? 'danger' : storm * 100 >= 60 ? 'warn' : '';

  const etaHrs = risk.eta_impact_hrs || 0;
  let etaLabel = '';
  if (etaHrs > 0) etaLabel = etaHrs < 24 ? `+${etaHrs}h` : `+${Math.round(etaHrs / 24)}d delay`;

  const showAlt = (sev === 'CRITICAL' || sev === 'HIGH')
               && risk.alternate_route
               && risk.alternate_route !== 'null';

  const leftStyle = sev === 'CRITICAL' ? 'border-left:2px solid rgba(248,81,73,0.50);'
                  : sev === 'HIGH'      ? 'border-left:2px solid rgba(251,133,0,0.40);'
                  : '';

  const statusLbl = status.replace(/_/g, ' ');

  const altHTML = showAlt ? `
    <div class="alt-block ${sev}">
      <div class="alt-block-header">
        <span class="alt-label">Alternate Route</span>
        ${etaLabel ? `<span class="eta-chip ${sev}">${etaLabel}</span>` : ''}
      </div>
      <span class="alt-route-text">${risk.alternate_route}</span>
    </div>` : '';

  const waypointHTML = formatWaypoint(ws, sev);

  return `
    <article class="card" data-severity="${sev}" data-shipment-id="${risk.shipment_id || ''}" style="${leftStyle}">
      <div class="card-top">
        <span class="vessel-name">${risk.vessel}</span>
        <span class="sev-badge ${sev}"><span class="sev-dot"></span>${sev}</span>
      </div>
      <div class="card-route-row">
        <span class="route-label">
          ${risk.origin}<span class="route-arrow"> &rarr; </span>${risk.destination}
        </span>
        <span class="status-pill ${status}">${statusLbl}</span>
      </div>
      <div class="metrics-grid">
        <div class="metric-tile">
          <span class="metric-label">Max Wind 72hr</span>
          <span class="metric-value ${warnCls}"><span>${Math.round(wind)}</span><span class="metric-unit">kn</span></span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Storm Probability</span>
          <span class="metric-value ${stormCls}"><span>${stormPct}</span><span class="metric-unit">%</span></span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Wave Height</span>
          <span class="metric-value"><span>${wave}</span><span class="metric-unit">m</span></span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Cargo</span>
          <span class="metric-value cargo">${risk.cargo}</span>
        </div>
      </div>
      ${waypointHTML}
      <p class="reasoning">${risk.reasoning}</p>
      ${altHTML}
    </article>`;
}

// ── Card rendering (dashboard) ────────────────────────────────────────────────

function renderCards(risks) {
  const grid   = document.getElementById('grid');
  const sorted = [...risks].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
  );

  grid.innerHTML = sorted.map(r => buildCard(r)).join('');

  // Count-up animation for metric values
  grid.querySelectorAll('.metric-value:not(.cargo)').forEach(el => {
    const spanEl = el.querySelector('span:first-child');
    if (!spanEl) return;
    const target = parseFloat(spanEl.textContent);
    if (isNaN(target) || target === 0) return;
    const isDecimal = spanEl.textContent.includes('.');
    const decimals  = isDecimal ? (spanEl.textContent.split('.')[1] || '').length : 0;
    let current = 0;
    const step  = target / 24;
    const timer = setInterval(() => {
      current = Math.min(current + step, target);
      spanEl.textContent = isDecimal ? current.toFixed(decimals) : Math.round(current);
      if (current >= target) clearInterval(timer);
    }, 16);
  });

  // Staggered entry
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!prefersReduced) {
    const cards = grid.querySelectorAll('.card');
    cards.forEach((card, i) => {
      setTimeout(() => card.classList.add('visible'), i * 80);
    });
  }

  // Slack toast for CRITICAL/HIGH
  const hasUrgent = risks.some(r => r.severity === 'CRITICAL' || r.severity === 'HIGH');
  if (hasUrgent) showSlackToast();
}

// ── Run analysis ──────────────────────────────────────────────────────────────

window.runAnalysis = async function runAnalysis() {
  if (ACTIVE_VIEW === 'stream') return window.runStreaming();
  if (ACTIVE_VIEW === 'routes') window.switchView('dashboard');

  const btnRun = document.getElementById('btnRun');
  const grid   = document.getElementById('grid');

  btnRun.textContent = 'Analyzing…';
  btnRun.classList.add('loading');
  showSkeletons(grid);

  try {
    await new Promise(resolve => setTimeout(resolve, 900));
    const risks  = await fetchRisks();
    const sorted = [...risks].sort(
      (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
    );
    renderCards(sorted);
    updateStatusCounts(sorted);
    updateSubtitle(sorted);
    document.getElementById('lastUpdated').textContent =
      'Last updated ' + new Date().toLocaleTimeString();
  } catch (err) {
    grid.innerHTML = `
      <div class="state-placeholder">
        <span style="color:var(--critical)">&#9888;</span>
        <span style="color:var(--text-secondary)">Analysis failed: ${err.message}</span>
      </div>`;
  } finally {
    btnRun.textContent = 'Run Analysis';
    btnRun.classList.remove('loading');
  }
};

// ── View switching ────────────────────────────────────────────────────────────

window.switchView = function switchView(mode) {
  ACTIVE_VIEW = mode;

  const dashView   = document.getElementById('dashView');
  const routesView = document.getElementById('routesView');
  const streamView = document.getElementById('streamView');
  const btnDash    = document.getElementById('btnDashView');
  const btnRoutes  = document.getElementById('btnRoutesView');
  const btnStream  = document.getElementById('btnStreamView');

  [btnDash, btnRoutes, btnStream].forEach(b => b.classList.remove('active'));

  if (mode === 'stream') {
    dashView.classList.add('hidden');
    routesView.classList.remove('active');
    streamView.classList.add('active');
    btnStream.classList.add('active');
  } else if (mode === 'routes') {
    dashView.classList.add('hidden');
    streamView.classList.remove('active');
    routesView.classList.add('active');
    btnRoutes.classList.add('active');
    renderRouteList();
  } else {
    // dashboard
    dashView.classList.remove('hidden');
    streamView.classList.remove('active');
    routesView.classList.remove('active');
    btnDash.classList.add('active');
  }
};

// ── Routes page ───────────────────────────────────────────────────────────────

function statusLabel(s) {
  return s.replace(/_/g, ' ');
}

function buildRouteRow(shipment, isNew) {
  const st      = shipment.status || 'IN_TRANSIT';
  const etaDays = shipment.eta_days != null ? `${shipment.eta_days}d` : '—';
  const cls     = isNew ? ' entering' : '';
  return `
    <div class="route-row${cls}" data-shipment-id="${shipment.shipment_id}">
      <span class="route-vessel">${shipment.vessel}</span>
      <span class="route-leg">${shipment.origin} &rarr; ${shipment.destination}</span>
      <span class="route-cargo">${shipment.cargo}</span>
      <span class="status-pill ${st}">${statusLabel(st)}</span>
      <span class="route-eta">${etaDays}</span>
      <button class="btn-delete"
              onclick="window.deleteRoute('${shipment.shipment_id}')"
              aria-label="Delete route ${shipment.shipment_id}">
        <span class="btn-delete-icon">&times;</span>
        <span class="btn-delete-text">Delete</span>
      </button>
    </div>`;
}

function checkRouteEmpty() {
  const list = document.getElementById('routeList');
  if (!list) return;
  const rows = list.querySelectorAll('.route-row');
  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className   = 'route-empty';
    empty.textContent = 'No active routes. Add one below.';
    list.appendChild(empty);
  }
}

function renderRouteList() {
  const list = document.getElementById('routeList');
  if (!list) return;

  // Preserve the static header row, rebuild everything else
  const header = list.querySelector('.route-list-header');
  list.innerHTML = '';
  if (header) list.appendChild(header);

  if (SHIPMENTS.length === 0) {
    const empty = document.createElement('div');
    empty.className   = 'route-empty';
    empty.textContent = 'No active routes. Add one below.';
    list.appendChild(empty);
    return;
  }

  SHIPMENTS.forEach(s => {
    const tmp     = document.createElement('div');
    tmp.innerHTML = buildRouteRow(s, false).trim();
    list.appendChild(tmp.firstElementChild);
  });
}

function parseWaypoints(str) {
  if (!str || !str.trim()) return [[0, 0], [0, 0], [0, 0]];
  return str.split('|').map(pair => {
    const parts = pair.trim().split(',');
    return [parseFloat(parts[0]) || 0, parseFloat(parts[1]) || 0];
  });
}

window.addRoute = function addRoute(e) {
  e.preventDefault();

  const form   = document.getElementById('addRouteForm');
  const vessel = form.querySelector('[name="vessel"]');
  const origin = form.querySelector('[name="origin"]');
  const dest   = form.querySelector('[name="destination"]');
  const cargo  = form.querySelector('[name="cargo"]');
  const etaInp = form.querySelector('[name="eta_days"]');
  const waypts = form.querySelector('[name="waypoints"]');

  [vessel, origin, dest, cargo, etaInp].forEach(el => el.classList.remove('invalid'));

  let valid = true;
  [vessel, origin, dest, cargo, etaInp].forEach(el => {
    if (!el.value.trim()) { el.classList.add('invalid'); valid = false; }
  });
  if (!valid) return;

  const id = `SH-${String(nextShipmentNum).padStart(2, '0')}`;
  nextShipmentNum++;

  const newShipment = {
    shipment_id: id,
    vessel:      vessel.value.trim(),
    origin:      origin.value.trim(),
    destination: dest.value.trim(),
    cargo:       cargo.value.trim(),
    status:      'IN_TRANSIT',
    eta_days:    parseInt(etaInp.value, 10),
    waypoints:   parseWaypoints(waypts ? waypts.value : ''),
  };

  SHIPMENTS.push(newShipment);

  const list    = document.getElementById('routeList');
  const emptyEl = list && list.querySelector('.route-empty');
  if (emptyEl) emptyEl.remove();

  if (list) {
    const tmp     = document.createElement('div');
    tmp.innerHTML = buildRouteRow(newShipment, true).trim();
    const row     = tmp.firstElementChild;
    list.appendChild(row);

    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!prefersReduced) {
      row.addEventListener('animationend', () => row.classList.remove('entering'), { once: true });
    }
  }

  form.reset();
  refreshRouteCount();
  showToast(`Route ${id} added`);
};

window.deleteRoute = function deleteRoute(shipmentId) {
  SHIPMENTS = SHIPMENTS.filter(s => s.shipment_id !== shipmentId);

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Remove matching dashboard card if visible
  const card = document.querySelector(`.card[data-shipment-id="${shipmentId}"]`);
  if (card) {
    if (prefersReduced) {
      card.remove();
    } else {
      card.style.transition = 'opacity 200ms ease-out, transform 200ms ease-out';
      card.style.opacity    = '0';
      card.style.transform  = 'translateY(-4px)';
      setTimeout(() => card.remove(), 220);
    }
  }

  // Remove route row
  const row = document.querySelector(`.route-row[data-shipment-id="${shipmentId}"]`);
  if (row) {
    row.style.pointerEvents = 'none';
    if (prefersReduced) {
      row.remove();
      checkRouteEmpty();
    } else {
      row.style.transition = 'opacity 180ms ease-out, transform 180ms ease-out';
      row.style.opacity    = '0';
      row.style.transform  = 'translateY(-4px)';
      setTimeout(() => { row.remove(); checkRouteEmpty(); }, 200);
    }
  }

  refreshRouteCount();
  showToast(`Route ${shipmentId} removed`);
};

// ── OpenUI streaming ──────────────────────────────────────────────────────────

function demoStream() {
  const src = window.__OPENUI_DEMO_SOURCE__;
  if (!src) {
    console.error('[ShipGuard] __OPENUI_DEMO_SOURCE__ not found — run gen_demo_js.py');
    return;
  }

  const grid    = document.getElementById('streamGrid');
  const counter = document.getElementById('tokenCount');
  grid.innerHTML = '';
  let tokenCount = 0;

  const renderer = window.ShipGuardOpenUI.library.createRenderer(grid);
  renderer.onCard = () => {
    document.getElementById('lastUpdated').textContent =
      'Streamed at ' + new Date().toLocaleTimeString();
  };

  const CHUNK = 6;
  let pos = 0;
  function tick() {
    if (pos >= src.length) { renderer.flush(); return; }
    const chunk = src.slice(pos, pos + CHUNK);
    pos += CHUNK;
    tokenCount++;
    counter.textContent = tokenCount;
    renderer.push(chunk);
    requestAnimationFrame(tick);
  }
  tick();
}

async function liveStream() {
  const grid    = document.getElementById('streamGrid');
  const counter = document.getElementById('tokenCount');
  grid.innerHTML = '';
  let tokenCount = 0;

  const renderer = window.ShipGuardOpenUI.library.createRenderer(grid);
  renderer.onCard = () => {
    document.getElementById('lastUpdated').textContent =
      'Streamed at ' + new Date().toLocaleTimeString();
  };

  const body = {
    shipments: SHIPMENTS.map(s => ({
      shipment_id: s.shipment_id,
      vessel:      s.vessel,
      origin:      s.origin,
      destination: s.destination,
      cargo:       s.cargo,
    })),
  };

  const res = await fetch(STREAM_URL, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });

  if (!res.ok) throw new Error(`Stream HTTP ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let   buf     = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const msg = JSON.parse(line.slice(6));
        if (msg.type === 'token') {
          tokenCount++;
          counter.textContent = tokenCount;
          renderer.push(msg.content);
        }
      } catch (_) { /* partial line */ }
    }
  }
  renderer.flush();
}

window.runStreaming = async function runStreaming() {
  const btnRun = document.getElementById('btnRun');
  btnRun.textContent = 'Streaming…';
  btnRun.classList.add('loading');

  document.getElementById('tokenCount').textContent = '0';

  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get('demo') === 'stream' || USE_MOCK_JS) {
      demoStream();
      return;
    }
    await liveStream();
  } catch (err) {
    document.getElementById('streamGrid').innerHTML = `
      <div class="state-placeholder">
        <span style="color:var(--critical)">&#9888;</span>
        <span style="color:var(--text-secondary)">Stream failed: ${err.message}</span>
      </div>`;
  } finally {
    btnRun.textContent = 'Run Analysis';
    btnRun.classList.remove('loading');
  }
};

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('load', function () {
  initModeIndicator();
  renderRouteList();
  window.runAnalysis();
});
