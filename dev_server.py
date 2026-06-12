#!/usr/bin/env python3
"""
Dev server for ClearPath dashboard.
Usage: python dev_server.py [port]
Default port: 8080
"""
import http.server
import sys
import urllib.parse
from pathlib import Path

BASE = Path(__file__).parent

ROUTES = {
    "/":                        BASE / "templates" / "index.html",
    "/static/app.js":           BASE / "static"    / "app.js",
    "/static/openui-library.js":BASE / "static"    / "openui-library.js",
    "/static/openui-demo.js":   BASE / "static"    / "openui-demo.js",
    "/mocks/llm_decision.json": BASE / "mocks"     / "llm_decision.json",
    "/mocks/db_output.json":    BASE / "mocks"     / "db_output.json",
    "/mocks/jua_weather.json":  BASE / "mocks"     / "jua_weather.json",
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else "?"
        print(f"  {self.command:<4} {self.path:<40} {status}")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        file_path = ROUTES.get(path)

        if file_path is None or not file_path.exists():
            self.send_error(404, f"Not found: {path}")
            return

        body         = file_path.read_bytes()
        content_type = MIME.get(file_path.suffix, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port   = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = http.server.HTTPServer(("", port), Handler)
    print(f"\n  ClearPath dev server")
    print(f"  http://localhost:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
