"""Small snapshot-only HTTP server, independent of web frameworks.

It serves the packaged laranex-schema-viewer-ui build (static/viewer, files listed in its manifest only) and the
configured laranex-schema 2.x document as /snapshot.json, validated and in canonical form, reloaded on each request.
It binds to loopback only, unless the caller explicitly allows a remote host: it has no authentication and no TLS.
"""

from __future__ import annotations

import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .contract import canonical_json
from .snapshot import read_document

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".woff": "font/woff", ".woff2": "font/woff2",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".json": "application/json; charset=utf-8"}
POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; "
          "base-uri 'none'; frame-ancestors 'none'")
REMOTE_WARNING = ("Warning: --unsafe-allow-remote serves the schema to anyone who can reach {host}:{port}, with no "
                  "authentication and no TLS. Host checking is not access control. Keep the server on 127.0.0.1 and "
                  "put an authenticated HTTPS reverse proxy or an access-controlled tunnel in front of it instead.")


def is_loopback(host: str) -> bool:
    """127.0.0.0/8, ::1 and the name localhost; any other host or name may be reachable from other machines."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def viewer_routes() -> dict[str, tuple[str, str]]:
    """URL path -> (packaged file, content type), for exactly the files the viewer's manifest lists."""
    viewer = files("laranex_schema_viewer").joinpath("static/viewer")
    manifest = json.loads(viewer.joinpath("manifest.json").read_text())
    routes = {}
    for entry in manifest["files"]:
        suffix = Path(entry["path"]).suffix
        if suffix in TYPES and suffix != ".json":
            routes["/" + entry["path"]] = (entry["path"], TYPES[suffix])
    routes["/"] = routes["/index.html"]
    return routes


def create_server(snapshot: str | Path, host: str = "127.0.0.1", port: int = 8765, allow_remote: bool = False):
    if not (is_loopback(host) or allow_remote):
        raise ValueError(f"{host!r} is not a loopback address; the viewer has no authentication and binds to "
                         "loopback only (see docs/cli.md#serve)")
    path = Path(snapshot).resolve()
    read_document(path)  # Fail before binding if the document is missing, invalid or of another version.
    viewer = files("laranex_schema_viewer").joinpath("static/viewer")
    routes = viewer_routes()

    class Handler(BaseHTTPRequestHandler):
        # Errors carry only their status, never schema data or request details.
        error_message_format = "%(code)d %(message)s\n"
        error_content_type = "text/plain; charset=utf-8"

        def end_headers(self):
            # Every response, errors included (send_error, and the base class's 400, 414 and 501).
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", POLICY)
            super().end_headers()

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
                    content = canonical_json(read_document(path).data).encode("utf-8")
                except (ValueError, OSError):
                    self.send_error(503, "Snapshot unavailable or invalid")
                    return
                mime = TYPES[".json"]
            elif route in routes:
                name, mime = routes[route]
                content = viewer.joinpath(name).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if not head:
                self.wfile.write(content)

    server = ThreadingHTTPServer((host, port), Handler, bind_and_activate=False)
    # The viewer requests its scripts, styles and fonts in parallel; socketserver's default backlog of 5 makes the
    # operating system reset the connections that overflow it.
    server.request_queue_size = 128
    server.daemon_threads = True
    try:
        server.server_bind()
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    return server
