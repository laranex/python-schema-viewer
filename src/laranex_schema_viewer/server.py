"""Small snapshot-only HTTP server, independent of web frameworks."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .snapshot import read_snapshot, snapshot_bytes


def create_server(snapshot: str | Path, host: str = "127.0.0.1", port: int = 8765):
    path = Path(snapshot).resolve()
    read_snapshot(path)  # Fail before binding if the snapshot is missing or invalid.
    assets = files("laranex_schema_viewer").joinpath("static")
    routes = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, head: bool = False):
            hostname = urlsplit("//" + self.headers.get("Host", "")).hostname
            if hostname not in {host, "localhost", "127.0.0.1", "::1"}:
                self.send_error(403, "Host not allowed")
                return
            route = urlsplit(self.path).path
            if route == "/snapshot.json":
                try:
                    content = snapshot_bytes(read_snapshot(path))
                except (ValueError, OSError):
                    self.send_error(503, "Snapshot unavailable or invalid")
                    return
                mime = "application/json; charset=utf-8"
            elif route in routes:
                name, mime = routes[route]
                content = assets.joinpath(name).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(content)

    return ThreadingHTTPServer((host, port), Handler)

