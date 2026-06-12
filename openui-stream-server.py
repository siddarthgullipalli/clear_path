#!/usr/bin/env python3
"""
ClearPath OpenUI Stream Server
Replaces dev_server.py for the OpenUI demo.

Routes:
  GET  /                      → templates/index.html
  GET  /static/*              → static/*
  GET  /mocks/*               → mocks/*
  POST /api/stream-risk       → SSE stream of OpenUI Lang tokens (mock)
  POST /api/analyze-stream    → alias of above (for when Person 1's backend is ready)

Usage:
  python openui-stream-server.py [port]   (default 8090)

  Run alongside dev_server.py (which serves port 8080).
  Set STREAM_PORT in app.js to match.
"""
import http.server
import json
import sys
import time
import urllib.parse
from pathlib import Path

BASE           = Path(__file__).parent
SEVERITY_ORDER = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}

STATIC_ROUTES = {
    '/':                        BASE / 'templates' / 'index.html',
    '/static/app.js':           BASE / 'static'    / 'app.js',
    '/static/openui-library.js':BASE / 'static'    / 'openui-library.js',
    '/mocks/llm_decision.json': BASE / 'mocks'     / 'llm_decision.json',
    '/mocks/db_output.json':    BASE / 'mocks'     / 'db_output.json',
    '/mocks/jua_weather.json':  BASE / 'mocks'     / 'jua_weather.json',
}

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
}

STREAM_PATHS = {'/api/stream-risk', '/api/analyze-stream'}


def risk_to_openui_lang(risk: dict) -> str:
    """Convert one RiskResult dict → OpenUI Lang <RiskCard> block."""
    ws        = risk.get('weather_snapshot', {})
    wind      = ws.get('wind_knots_max_72h', 0)
    storm     = ws.get('storm_probability',  0)
    wave      = ws.get('wave_height_m',      0)
    eta_hours = risk.get('eta_impact_hrs', 0)
    reasoning = risk.get('reasoning', '').strip()
    alt_route = (risk.get('alternate_route') or 'null').strip()

    attrs = (
        f'vessel="{risk["vessel"]}" '
        f'severity="{risk["severity"]}" '
        f'origin="{risk["origin"]}" '
        f'destination="{risk["destination"]}" '
        f'cargo="{risk["cargo"]}" '
        f'status="{risk["status"]}" '
        f'wind="{wind}" '
        f'storm="{storm}" '
        f'wave="{wave}" '
        f'eta_hours="{eta_hours}"'
    )

    return f'<RiskCard {attrs}>\n{reasoning}\n||\n{alt_route}\n</RiskCard>\n\n'


def build_openui_source() -> str:
    """Load mock LLM decisions and produce the full OpenUI Lang source."""
    mock_path = BASE / 'mocks' / 'llm_decision.json'
    risks     = json.loads(mock_path.read_text(encoding='utf-8'))
    risks.sort(key=lambda r: SEVERITY_ORDER.get(r.get('severity', 'LOW'), 99))
    return ''.join(risk_to_openui_lang(r) for r in risks)


class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else '?'
        print(f'  {self.command:<7} {self.path:<45} {status}')

    # ── OPTIONS (CORS preflight) ────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ── GET (static files) ──────────────────────────────────────────────────

    def do_GET(self):
        path      = urllib.parse.urlparse(self.path).path
        file_path = STATIC_ROUTES.get(path)

        if file_path is None or not file_path.exists():
            self.send_error(404, f'Not found: {path}')
            return

        body         = file_path.read_bytes()
        content_type = MIME.get(file_path.suffix, 'application/octet-stream')

        self.send_response(200)
        self.send_header('Content-Type',   content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control',  'no-cache')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ── POST (streaming endpoint) ───────────────────────────────────────────

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path in STREAM_PATHS:
            self._stream_openui()
        else:
            self.send_error(404, f'Not found: {path}')

    def _stream_openui(self):
        try:
            source = build_openui_source()
        except Exception as exc:
            self.send_error(500, f'Failed to build OpenUI source: {exc}')
            return

        self.send_response(200)
        self.send_header('Content-Type',  'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection',    'keep-alive')
        self._cors()
        self.end_headers()

        # ── Token streaming ──────────────────────────────────────────────────
        # Chunk size of 6 chars ≈ ~1 LLM token.
        # 30ms delay between chunks gives a realistic streaming feel
        # and lets the frontend render cards as </RiskCard> tags arrive.
        CHUNK   = 6
        DELAY_S = 0.030

        try:
            for i in range(0, len(source), CHUNK):
                chunk   = source[i : i + CHUNK]
                payload = json.dumps({'type': 'token', 'content': chunk})
                self.wfile.write(f'data: {payload}\n\n'.encode('utf-8'))
                self.wfile.flush()
                time.sleep(DELAY_S)

            self.wfile.write(b'data: {"type":"done"}\n\n')
            self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected — normal for SSE

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')


if __name__ == '__main__':
    port   = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    server = http.server.HTTPServer(('', port), Handler)

    total_chars  = len(build_openui_source())
    stream_secs  = round(total_chars / 6 * 0.030, 1)

    print(f'\n  ClearPath OpenUI Stream Server')
    print(f'  http://localhost:{port}')
    print(f'  POST /api/stream-risk  →  ~{total_chars} chars, ~{stream_secs}s stream\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')
