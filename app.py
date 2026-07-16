#!/usr/bin/env python3
"""Web app for the sports win index.

Reads the per-league score CSVs and serves a small page that lets you pick
one team from each league and see how many wins those teams have racked up
across a chosen month.

Stdlib only (``http.server``, ``json``) — no Flask, no extra dependency,
in keeping with the project's one-dependency constraint (plan 9.4). The
win math lives in ``win_index.py``; this module is just HTTP glue.

Usage:
    python app.py                 # serve on http://localhost:8000
    python app.py --port 5000
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import win_index

STATIC_DIR = Path(__file__).resolve().parent / "web"


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        route = urlparse(self.path)
        if route.path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif route.path == "/api/config":
            self._send_json({
                "leagues": list(win_index.LEAGUES),
                "teams": win_index.load_team_names(),
                "months": win_index.available_months(),
                "defaults": win_index.load_default_selections(),
            })
        elif route.path == "/api/wins":
            self._handle_wins(parse_qs(route.query))
        else:
            self.send_error(404, "Not found")

    def _handle_wins(self, params: dict) -> None:
        months = win_index.available_months()
        month = (params.get("month", [None])[0]) or (months[0] if months else "")
        if not month:
            self._send_json({"month": "", "total": 0, "teams": []})
            return
        selections = {
            league: params[league][0]
            for league in win_index.LEAGUES
            if params.get(league) and params[league][0]
        }
        self._send_json(win_index.wins_summary(selections, month))

    def log_message(self, *args) -> None:  # keep the console quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Sports win index running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
