const USE_MOCK_JS   = true;         // flip to false at T+2:30 when backend is ready
let   ACTIVE_VIEW   = 'dashboard';  // 'dashboard' | 'stream'
const STREAM_PORT   = 8090;
const STREAM_URL    = `http://localhost:${STREAM_PORT}/api/stream-risk`;

const SEVERITY_ORDER = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };

const SEV_COLORS = {
  CRITICAL: '#f85149',
  HIGH:     '#fb8500',
  MEDIUM:   '#d29922',
  LOW:      '#3fb950',
};

const BTN_ICON_SVG = `<svg class="btn-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
  <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Z" fill="currentColor" opacity="0.4"/>
  <path d="M6.25 5.25 11 8l-4.75 2.75v-5.5Z" fill="currentColor"/>
</svg>`;

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatTime(date) {
  return date.toLocaleTimeString('en-US', {
    hour:   '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function statusLabel(status) {
  return status.replace(/_/g, ' ');
}

function etaLabel(hrs) {
  if (!hrs) return null;
  if (hrs < 24) return `+${hrs}h`;
  return `+${Math.round(hrs / 24)}d delay`;
}

function windClass(knots) {
  if (knots >= 75) return 'danger';
  if (knots >= 60) return 'warn';
  return '';
}

function stormClass(pct) {
  if (pct >= 80) return 'danger';
  if (pct >= 60) return 'warn';
  return '';
}

// ── (4) Waypoint callout ─────────────────────────────────────────────────────
// Reads worst_waypoint from weather_snapshot. CRITICAL/HIGH only.
// Negative lat = S, positive = N. Negative lon = W, positive = E.

function formatWaypoint(ws, sev) {
  if (sev !== 'CRITICAL' && sev !== 'HIGH') return '';
  const wp = ws && ws.worst_waypoint;
  if (!Array.isArray(wp) || wp.length < 2) return '';
  const lat    = wp[0];
  const lon    = wp[1];
  const latStr = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? 'N' : 'S'}`;
  const lonStr = `${Math.abs(lon).toFixed(1)}°${lon >= 0 ? 'E' : 'W'}`;
  const color  = sev === 'CRITICAL' ? '#f85149' : '#d29922';
  return `<p class="waypoint-callout" style="color:${color}">⚠ Risk peak at ${latStr}, ${lonStr}</p>`;
}

// ── Count-up animation ───────────────────────────────────────────────────────

function countUp(el, target, decimals) {
  const duration = 850;
  const start    = performance.now();

  function fmt(v) {
    return decimals > 0 ? v.toFixed(decimals) : String(Math.round(v));
  }

  function tick(now) {
    const t      = Math.min((now - start) / duration, 1);
    const eased  = 1 - Math.pow(1 - t, 4); // exponential ease-out
    el.textContent = fmt(eased * target);
    if (t < 1) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

// ── (3) Skeleton loading state ───────────────────────────────────────────────
// Fills any grid container with 5 skeleton cards while data is fetching.
// Each block pulses opacity only — no background-position trick.

function showSkeletons(gridEl) {
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
  gridEl.innerHTML = Array(5).fill(one).join('');
}

// ── (2) Slack toast ──────────────────────────────────────────────────────────
// Slides in from right 500ms after call, stays 4s, slides out.
// Transition lives on .slack-toast base — both enter and exit are animated.

function showSlackToast() {
  const toast = document.getElementById('slackToast');
  clearTimeout(toast._showTimer);
  clearTimeout(toast._hideTimer);
  toast._showTimer = setTimeout(() => {
    requestAnimationFrame(() => {
      toast.classList.add('visible');
      toast._hideTimer = setTimeout(() => toast.classList.remove('visible'), 4000);
    });
  }, 500);
}

// ── (5) Dynamic subtitle ─────────────────────────────────────────────────────
// "2 alerts active · 1 advisory · 2 clear" — updates after every run.

function updateSubtitle(risks) {
  const alerts   = risks.filter(r => r.severity === 'CRITICAL' || r.severity === 'HIGH').length;
  const advisory = risks.filter(r => r.severity === 'MEDIUM').length;
  const clear    = risks.filter(r => r.severity === 'LOW').length;
  const parts    = [];
  if (alerts   > 0) parts.push(`${alerts} ${alerts   === 1 ? 'alert'     : 'alerts'} active`);
  if (advisory > 0) parts.push(`${advisory} ${advisory === 1 ? 'advisory' : 'advisories'}`);
  if (clear    > 0) parts.push(`${clear} clear`);
  document.getElementById('headerSubtitle').textContent =
    parts.length > 0 ? parts.join(' · ') : '5 active shipments';
}

// ── Card builder ─────────────────────────────────────────────────────────────

function buildCard(risk) {
  const sev = risk.severity;
  const ws  = risk.weather_snapshot || {};

  const windKnots  = ws.wind_knots_max_72h || 0;
  const stormPct   = (ws.storm_probability || 0) * 100;
  const waveHeight = ws.wave_height_m || 0;

  const wClass = windClass(windKnots);
  const sClass = stormClass(stormPct);

  const showAlt = (sev === 'CRITICAL' || sev === 'HIGH') && risk.alternate_route;
  const etaText = etaLabel(risk.eta_impact_hrs);

  const altHTML = showAlt ? `
    <div class="alt-block ${sev}">
      <div class="alt-block-header">
        <span class="alt-label">Alternate Route</span>
        ${etaText ? `<span class="eta-chip ${sev}">${etaText}</span>` : ''}
      </div>
      <span class="alt-route-text">${risk.alternate_route}</span>
    </div>` : '';

  const waypointHTML = formatWaypoint(ws, sev);

  const reasonHTML = risk.reasoning
    ? `<p class="reasoning">${risk.reasoning}</p>`
    : '';

  return `
    <article class="card" data-severity="${sev}">

      <div class="card-top">
        <span class="vessel-name">${risk.vessel}</span>
        <span class="sev-badge ${sev}">
          <span class="sev-dot"></span>${sev}
        </span>
      </div>

      <div class="card-route-row">
        <span class="route-label">
          ${risk.origin}<span class="route-arrow"> &rarr; </span>${risk.destination}
        </span>
        <span class="status-pill ${risk.status}">${statusLabel(risk.status)}</span>
      </div>

      <div class="metrics-grid">
        <div class="metric-tile">
          <span class="metric-label">Max Wind 72hr</span>
          <span class="metric-value ${wClass}">
            <span class="num" data-count="${windKnots}" data-dec="0">${Math.round(windKnots)}</span>
            <span class="metric-unit">kn</span>
          </span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Storm Probability</span>
          <span class="metric-value ${sClass}">
            <span class="num" data-count="${stormPct}" data-dec="0">${Math.round(stormPct)}</span>
            <span class="metric-unit">%</span>
          </span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Wave Height</span>
          <span class="metric-value">
            <span class="num" data-count="${waveHeight}" data-dec="1">${waveHeight.toFixed(1)}</span>
            <span class="metric-unit">m</span>
          </span>
        </div>
        <div class="metric-tile">
          <span class="metric-label">Cargo</span>
          <span class="metric-value cargo">${risk.cargo}</span>
        </div>
      </div>

      ${waypointHTML}
      ${reasonHTML}
      ${altHTML}

    </article>`;
}

// ── Render ───────────────────────────────────────────────────────────────────

function renderCards(risks) {
  const sorted = [...risks].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
  );

  const grid = document.getElementById('grid');
  grid.innerHTML = sorted.map(buildCard).join('');

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cards = grid.querySelectorAll('.card');

  cards.forEach((card, i) => {
    if (prefersReduced) {
      card.classList.add('visible');
      return;
    }
    // setTimeout IS the stagger — animation fires at delay=0 when class is added
    setTimeout(() => {
      card.classList.add('visible');
      card.querySelectorAll('.num[data-count]').forEach(el => {
        const target   = parseFloat(el.dataset.count);
        const decimals = parseInt(el.dataset.dec ?? '0', 10);
        el.textContent = decimals > 0 ? '0.0' : '0';
        countUp(el, target, decimals);
      });
    }, i * 60);
  });

  // Severity count chips
  const counts = {};
  risks.forEach(r => { counts[r.severity] = (counts[r.severity] || 0) + 1; });

  document.getElementById('statusCounts').innerHTML = Object.entries(counts)
    .sort(([a], [b]) => SEVERITY_ORDER[a] - SEVERITY_ORDER[b])
    .map(([sev, n]) => `
      <span class="status-count">
        <span class="status-count-dot" style="background:${SEV_COLORS[sev]}"></span>
        ${n} ${sev}
      </span>`)
    .join('');

  document.getElementById('lastUpdated').textContent =
    `Last updated ${formatTime(new Date())}`;

  // (5) subtitle + (2) toast
  updateSubtitle(risks);
  if (risks.some(r => r.severity === 'CRITICAL' || r.severity === 'HIGH')) {
    showSlackToast();
  }
}

// ── Fetch ────────────────────────────────────────────────────────────────────

async function fetchRisks() {
  if (USE_MOCK_JS) {
    const res = await fetch('/mocks/llm_decision.json');
    if (!res.ok) throw new Error(`Mock fetch failed: ${res.status}`);
    return res.json();
  }
  const res = await fetch('/api/analyze');
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ── View switcher ─────────────────────────────────────────────────────────────

window.switchView = function switchView(mode) {
  ACTIVE_VIEW = mode;

  const dashView   = document.getElementById('dashView');
  const streamView = document.getElementById('streamView');
  const btnDash    = document.getElementById('btnDashView');
  const btnStream  = document.getElementById('btnStreamView');

  if (mode === 'stream') {
    dashView.classList.add('hidden');
    streamView.classList.add('active');
    btnDash.classList.remove('active');
    btnStream.classList.add('active');
  } else {
    dashView.classList.remove('hidden');
    streamView.classList.remove('active');
    btnDash.classList.add('active');
    btnStream.classList.remove('active');
  }
};

// ── Run Analysis (dispatches to dashboard or streaming path) ──────────────────

window.runAnalysis = async function runAnalysis() {
  if (ACTIVE_VIEW === 'stream') {
    return window.runStreaming();
  }

  const btn  = document.getElementById('btnRun');
  const grid = document.getElementById('grid');

  btn.classList.add('loading');
  btn.innerHTML = 'Analyzing&hellip;';
  showSkeletons(grid); // (3) skeleton replaces spinner

  try {
    const risks = await fetchRisks();
    renderCards(risks);
  } catch (err) {
    grid.innerHTML = `
      <div class="state-placeholder">
        <span style="color:#f85149">Error: ${err.message}</span>
      </div>`;
  } finally {
    btn.classList.remove('loading');
    btn.innerHTML = `${BTN_ICON_SVG} Run Analysis`;
  }
};

// ── OpenUI Streaming ──────────────────────────────────────────────────────────

window.runStreaming = async function runStreaming() {
  const btn        = document.getElementById('btnRun');
  const streamGrid = document.getElementById('streamGrid');
  const tokenCount = document.getElementById('tokenCount');

  btn.classList.add('loading');
  btn.innerHTML = '&#9889; Streaming&hellip;';
  showSkeletons(streamGrid); // (3) skeleton while waiting for first token
  tokenCount.textContent = '0';

  let cleared = false;
  let tokens  = 0;

  const library  = window.ClearPathOpenUI.library;
  let   renderer = null;

  try {
    const response = await fetch(STREAM_URL, { method: 'POST' });
    if (!response.ok) throw new Error(`Stream endpoint returned ${response.status}`);

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let   partial = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      partial += decoder.decode(value, { stream: true });

      const lines = partial.split('\n');
      partial = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === 'token') {
          if (!cleared) {
            streamGrid.innerHTML = '';
            renderer = library.createRenderer(streamGrid);
            cleared = true;
          }
          tokens++;
          tokenCount.textContent = String(tokens);
          renderer.push(event.content);

        } else if (event.type === 'done') {
          if (renderer) renderer.flush();
        }
      }
    }

    if (renderer) renderer.flush();

    // Severity count chips
    const streamedCards = streamGrid.querySelectorAll('.card[data-severity]');
    const counts = {};
    streamedCards.forEach(c => {
      const s = c.dataset.severity;
      counts[s] = (counts[s] || 0) + 1;
    });
    document.getElementById('statusCounts').innerHTML = Object.entries(counts)
      .sort(([a], [b]) => SEVERITY_ORDER[a] - SEVERITY_ORDER[b])
      .map(([sev, n]) => `
        <span class="status-count">
          <span class="status-count-dot" style="background:${SEV_COLORS[sev]}"></span>
          ${n} ${sev}
        </span>`)
      .join('');

    document.getElementById('lastUpdated').textContent =
      `Streamed at ${formatTime(new Date())} via OpenUI Lang`;

    // (5) subtitle + (2) toast
    const streamedRisks = Array.from(streamedCards).map(c => ({ severity: c.dataset.severity }));
    updateSubtitle(streamedRisks);
    if (streamedRisks.some(r => r.severity === 'CRITICAL' || r.severity === 'HIGH')) {
      showSlackToast();
    }

  } catch (err) {
    streamGrid.innerHTML = `
      <div class="state-placeholder" style="grid-column:1/-1">
        <span style="color:#f85149">Stream error: ${err.message}</span>
      </div>`;
  } finally {
    btn.classList.remove('loading');
    btn.innerHTML = `${BTN_ICON_SVG} Run Analysis`;
  }
};

// ── Demo-stream mode ──────────────────────────────────────────────────────────
// ?demo=stream → renders all 5 cards synchronously from pre-baked source.
// No fetch — works with headless Chrome screenshots.

window.demoStream = function demoStream() {
  window.switchView('stream');
  const streamGrid = document.getElementById('streamGrid');
  const tokenCount = document.getElementById('tokenCount');

  streamGrid.innerHTML = '';

  const library  = window.ClearPathOpenUI.library;
  const renderer = library.createRenderer(streamGrid);

  const source = window.__OPENUI_DEMO_SOURCE__ || '';
  renderer.push(source);
  renderer.flush();

  tokenCount.textContent = String(Math.round(source.length / 6));

  // Severity count chips
  const streamedCards = streamGrid.querySelectorAll('.card[data-severity]');
  const counts = {};
  streamedCards.forEach(c => {
    const s = c.dataset.severity;
    counts[s] = (counts[s] || 0) + 1;
  });
  document.getElementById('statusCounts').innerHTML = Object.entries(counts)
    .sort(([a], [b]) => SEVERITY_ORDER[a] - SEVERITY_ORDER[b])
    .map(([sev, n]) => `<span class="status-count">
      <span class="status-count-dot" style="background:${SEV_COLORS[sev]}"></span>
      ${n} ${sev}</span>`)
    .join('');

  document.getElementById('lastUpdated').textContent =
    `Demo stream rendered at ${formatTime(new Date())} via OpenUI Lang`;

  // (5) subtitle + (2) toast
  const streamedRisks = Array.from(streamedCards).map(c => ({ severity: c.dataset.severity }));
  updateSubtitle(streamedRisks);
  if (streamedRisks.some(r => r.severity === 'CRITICAL' || r.severity === 'HIGH')) {
    showSlackToast();
  }
};

window.onload = () => {
  // (1) Mode indicator — amber dot in demo, pulsing green dot when live
  const dot   = document.getElementById('modeDot');
  const label = document.getElementById('modeLabel');
  if (USE_MOCK_JS) {
    dot.classList.add('demo');
    label.textContent = 'Demo mode';
  } else {
    dot.classList.add('live');
    label.textContent = 'Live';
  }

  const params = new URLSearchParams(location.search);
  if (params.get('demo') === 'stream') {
    window.demoStream();
  } else {
    window.runAnalysis();
  }
};
