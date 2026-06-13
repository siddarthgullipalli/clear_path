# main.py
# Person 1 — Siddarth
# FastAPI server: serves dashboard + /api/risk and /api/run endpoints

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import json
import os
import time
import logging

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("clearpath")

app = FastAPI(title="ClearPath — Supply Chain Risk Agent")

# ── Config ──────────────────────────────────────────────────────
USE_MOCKS = os.getenv("USE_MOCKS", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
PORT = int(os.getenv("PORT", "8000"))

# CORS — configurable via CORS_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGINS],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (JS) — served from /static
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Templates (HTML) — served from /templates
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
HAS_TEMPLATES = os.path.exists(templates_dir)
if HAS_TEMPLATES:
    try:
        templates = Jinja2Templates(directory=templates_dir)
    except Exception as e:
        logger.warning("Jinja2Templates not available (%s), falling back to built-in HTML", e)
        HAS_TEMPLATES = False

# Mock data directory
mocks_dir = os.path.join(os.path.dirname(__file__), "mocks")
HAS_MOCKS = os.path.exists(mocks_dir)

# In-memory cache for agent results
_cached_results = []

# ── Request logging middleware ──────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(
        "%s %s → %d (%.4fs)",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response

# ── Startup event ───────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("═══════════════════════════════════════════════════")
    logger.info("ClearPath — Supply Chain Risk Agent starting...")
    logger.info("Mock mode: %s", USE_MOCKS)
    logger.info("Port: %s", PORT)
    logger.info("Templates available: %s", HAS_TEMPLATES)
    logger.info("Static files available: %s", os.path.exists(static_dir))
    logger.info("Mocks directory available: %s", HAS_MOCKS)
    logger.info("CORS origins: %s", CORS_ORIGINS)
    logger.info("═══════════════════════════════════════════════════")


@app.get("/health")
async def health():
    """Health check endpoint — returns server status and cache info."""
    import os as _os
    return {
        "status": "ok",
        "mock_mode": USE_MOCKS,
        "cached_results": len(_cached_results),
        "has_pioneer_key": bool(_os.getenv("PIONEER_API_KEY") or _os.getenv("OPENAI_API_KEY")),
        "has_telegram": bool(_os.getenv("TELEGRAM_BOT_TOKEN")),
        "has_clickhouse": bool(_os.getenv("CLICKHOUSE_HOST")),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main dashboard. If templates/ doesn't exist yet, show a fallback page."""
    if HAS_TEMPLATES:
        return templates.TemplateResponse("index.html", {"request": request})

    # Fallback — Person 3 hasn't delivered HTML yet
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ClearPath — Supply Chain Risk Agent</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: system-ui, -apple-system, sans-serif; background: #0f1117; color: #e2e8f0; padding: 32px 24px; max-width: 900px; margin: 0 auto; }
            .header { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
            .header .icon { font-size: 32px; }
            h1 { font-size: 22px; font-weight: 700; }
            .subtitle { color: #94a3b8; font-size: 12px; margin-bottom: 28px; line-height: 1.6; }
            .controls { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
            button { padding: 10px 22px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: opacity 0.15s; }
            button:disabled { opacity: 0.5; cursor: not-allowed; }
            .btn-run { background: #3b82f6; color: #fff; }
            .btn-run:hover:not(:disabled) { background: #2563eb; }
            .btn-reset { background: #334155; color: #cbd5e1; }
            .btn-reset:hover:not(:disabled) { background: #475569; }
            .btn-health { background: #065f46; color: #6ee7b7; font-size: 11px; padding: 8px 14px; }
            .results { background: #1a1f2e; border: 1px solid #2d3548; border-radius: 10px; padding: 18px; }
            .results h3 { font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
            pre { background: #111620; border-radius: 6px; padding: 16px; font-size: 11px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; max-height: 480px; overflow-y: auto; color: #a5b4fc; font-family: 'SF Mono', 'Fira Code', monospace; }
            .status-bar { display: flex; align-items: center; gap: 8px; margin-top: 14px; font-size: 12px; }
            .status-dot { width: 8px; height: 8px; border-radius: 50%; background: #334155; }
            .status-dot.ok { background: #22c55e; }
            .status-dot.err { background: #ef4444; }
            .status-dot.busy { background: #f59e0b; animation: pulse 1s infinite; }
            @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
            .meta { color: #64748b; font-size: 11px; }
        </style>
    </head>
    <body>
        <div class="header">
            <span class="icon">&#9888;&#65039;</span>
            <h1>ClearPath — Supply Chain Risk Agent</h1>
        </div>
        <p class="subtitle">Powered by Jua AI &middot; ClickHouse &middot; LangGraph &middot; Pioneer LLM &middot; Composio<br>Mock mode active — agent pipeline runs with sample data</p>

        <div class="controls">
            <button class="btn-run" id="runBtn" onclick="runAnalysis()">&#9654; Run Analysis</button>
            <button class="btn-reset" id="resetBtn" onclick="resetCache()">&#8635; Reset</button>
            <button class="btn-health" onclick="checkHealth()">Health</button>
        </div>

        <div class="results">
            <h3>Risk Results</h3>
            <pre id="output">Click "Run Analysis" to start the agent pipeline...</pre>
            <div class="status-bar">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText" class="meta">Idle</span>
            </div>
        </div>

        <script>
            function setStatus(state, text) {
                const dot = document.getElementById('statusDot');
                dot.className = 'status-dot ' + state;
                document.getElementById('statusText').textContent = text;
            }

            async function runAnalysis() {
                const btn = document.getElementById('runBtn');
                const out = document.getElementById('output');
                btn.disabled = true;
                btn.textContent = 'Analyzing...';
                setStatus('busy', 'Running agent pipeline...');
                out.textContent = 'Running agent pipeline...';
                try {
                    const res = await fetch('/api/run', { method: 'POST' });
                    const data = await res.json();
                    if (data.status === 'ok') {
                        const risk = await fetch('/api/risk');
                        const risks = await risk.json();
                        out.textContent = JSON.stringify(risks, null, 2);
                        setStatus('ok', 'Done — ' + data.count + ' shipments processed');
                    } else {
                        out.textContent = JSON.stringify(data, null, 2);
                        setStatus('err', 'Error: ' + (data.detail || 'Unknown'));
                    }
                } catch(e) {
                    out.textContent = 'Network error: ' + e.message;
                    setStatus('err', 'Connection failed');
                } finally {
                    btn.disabled = false;
                    btn.textContent = '\u25b6 Run Analysis';
                }
            }

            async function resetCache() {
                const btn = document.getElementById('resetBtn');
                btn.disabled = true;
                try {
                    const res = await fetch('/api/reset', { method: 'POST' });
                    const data = await res.json();
                    document.getElementById('output').textContent = 'Cache cleared (' + data.cleared + ' entries removed)';
                    setStatus('ok', 'Reset — ' + data.cleared + ' entries cleared');
                } catch(e) {
                    setStatus('err', 'Reset failed: ' + e.message);
                } finally {
                    btn.disabled = false;
                }
            }

            async function checkHealth() {
                try {
                    const res = await fetch('/health');
                    const data = await res.json();
                    const out = document.getElementById('output');
                    out.textContent = JSON.stringify(data, null, 2);
                    setStatus('ok', 'Server healthy');
                } catch(e) {
                    setStatus('err', 'Health check failed: ' + e.message);
                }
            }
        </script>
    </body>
    </html>
    """)


@app.get("/api/risk")
async def get_risk():
    """
    Return the latest risk results (RiskResult list, section 5.4).
    Person 3's JS polls this every 10-15 seconds.
    """
    if _cached_results:
        return _cached_results

    if USE_MOCKS and HAS_MOCKS:
        mock_path = os.path.join(mocks_dir, "llm_decision.json")
        if os.path.exists(mock_path):
            with open(mock_path) as f:
                return json.load(f)

    return _cached_results


@app.post("/api/run")
async def run():
    """Trigger the agent pipeline and cache results."""
    global _cached_results
    try:
        from agent import run_agent
        logger.info("Agent run triggered...")
        _cached_results = run_agent()
        logger.info("Agent run complete — %d results cached", len(_cached_results))
        return {"status": "ok", "count": len(_cached_results)}
    except ImportError as e:
        logger.error("Failed to import agent module: %s", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": f"Agent module not available: {e}"},
        )
    except Exception as e:
        logger.exception("Agent run failed with unexpected error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": f"Agent error: {e}"},
        )


@app.post("/api/reset")
async def reset():
    """Clear cached results — useful for demo resets between runs."""
    global _cached_results
    count = len(_cached_results)
    _cached_results = []
    logger.info("Cache reset — cleared %d cached results", count)
    return {"status": "ok", "cleared": count}


# ── Serve mock files for Person 3's frontend dev ────────────────
@app.get("/mocks/{filename}")
async def serve_mock(filename: str):
    """Allow Person 3's frontend to fetch mock data directly during dev."""
    if not HAS_MOCKS:
        return JSONResponse(
            status_code=404,
            content={"error": "mocks directory not found"},
        )
    mock_path = os.path.join(mocks_dir, filename)
    if os.path.exists(mock_path):
        return FileResponse(mock_path)
    return JSONResponse(
        status_code=404,
        content={"error": f"mock file '{filename}' not found"},
    )


# ── Startup ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting ClearPath server on http://0.0.0.0:%s", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
