import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from laranex_schema_viewer.server import create_server
from laranex_schema_viewer.snapshot import write_document
from tests.test_extraction import example_document


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        write_document(example_document(), self.path)

    def run_cli(self, *args, cwd=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(Path("src").resolve()))
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", *args],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=20)

    def test_cli_build_and_failed_build_preserves_snapshot(self):
        result = self.run_cli("build", "--module", "tests.fixtures.sqlalchemy_models",
                              "--output", str(self.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("6 tables", result.stdout)
        before = self.path.read_bytes()
        result = self.run_cli("build", "--module", "nonexistent_module", "--output", str(self.path))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_serve_and_mcp_import_no_build_or_orm(self):
        program = (
            "import sys\n"
            "class Block:\n"
            " def find_spec(self, fullname, *args):\n"
            "  if fullname.split('.')[0] in {'sqlalchemy','sqlmodel'} or fullname == 'laranex_schema_viewer.build':\n"
            "   raise AssertionError('Forbidden application-loading import: '+fullname)\n"
            "sys.meta_path.insert(0, Block())\n"
            "import laranex_schema_viewer.server\n"
            "from laranex_schema_viewer.mcp import create_server\n"
            "create_server(sys.argv[1])\n"
        )
        result = subprocess.run([sys.executable, "-c", program, str(self.path)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_protocol_routes_without_network(self):
        # Exercise the real HTTP request parser/handler using byte streams.
        # No listening socket is needed in restricted environments.
        with patch("laranex_schema_viewer.server.ThreadingHTTPServer") as constructor:
            create_server(self.path)
            handler = constructor.call_args.args[1]

        def request(target, method="GET", host="localhost"):
            output = io.BytesIO()

            class Connection:
                def makefile(self, *args):
                    return io.BytesIO(f"{method} {target} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())

                def sendall(self, data):
                    output.write(data)

            handler(Connection(), ("127.0.0.1", 10000), None)
            return output.getvalue()

        from laranex_schema_viewer.server import POLICY, viewer_routes

        routes = viewer_routes()
        self.assertGreater(len(routes), 10)
        for route in [*routes, "/snapshot.json"]:
            response = request(route)
            self.assertIn(b"200 OK", response)
            self.assertIn(b"X-Content-Type-Options: nosniff", response)
            self.assertIn(f"Content-Security-Policy: {POLICY}".encode(), response)
        self.assertIn(b"Content-Type: font/woff2", request(next(r for r in routes if r.endswith(".woff2"))))
        # The saved document, validated and in canonical form.
        body = request("/snapshot.json").split(b"\r\n\r\n", 1)[1]
        self.assertEqual(body, self.path.read_bytes())
        self.assertEqual((json.loads(body)["format"], json.loads(body)["version"]), ("laranex-schema", "2.0"))
        for route in ["/../pyproject.toml", "/%2e%2e/pyproject.toml", "/.env", "/manifest.json",
                      "/LICENSE.txt", "/graph.js", "/styles.css", "/assets/", "/assets/../manifest.json"]:
            self.assertIn(b"404 Not Found", request(route))
        for icon, mime in (("/favicon.ico", b"image/x-icon"), ("/favicon.svg", b"image/svg+xml")):
            self.assertIn(b"Content-Type: " + mime, request(icon))
        self.assertIn(b"403", request("/", host="untrusted.example"))
        self.assertIn(b"501", request("/snapshot.json", method="POST"))
        self.assertEqual(request("/snapshot.json", method="HEAD").split(b"\r\n\r\n")[1], b"")
        self.path.write_text("invalid")
        self.assertIn(b"503", request("/snapshot.json"))
        self.path.write_text(json.dumps({"format": "laranex-schema", "version": "1.0"}))
        self.assertIn(b"503", request("/snapshot.json"))

    def test_every_response_carries_the_security_headers_and_errors_carry_no_schema(self):
        from laranex_schema_viewer.server import POLICY

        with patch("laranex_schema_viewer.server.ThreadingHTTPServer") as constructor:
            create_server(self.path)
            handler = constructor.call_args.args[1]

        def request(raw: bytes) -> tuple[bytes, bytes]:
            output = io.BytesIO()

            class Connection:
                def makefile(self, *args):
                    return io.BytesIO(raw)

                def sendall(self, data):
                    output.write(data)

            handler(Connection(), ("127.0.0.1", 10000), None)
            head, _, body = output.getvalue().partition(b"\r\n\r\n")
            return head, body

        def get(target, host="localhost", method="GET"):
            return request(f"{method} {target} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())

        names = [t.name.encode() for t in example_document().tables()]
        self.assertTrue(names)
        valid = self.path.read_bytes()
        cases = {"200": get("/snapshot.json"), "200 page": get("/"), "403": get("/snapshot.json", host="evil.example"),
                 "404": get("/snapshot.json/x"), "501": get("/snapshot.json", method="POST"),
                 "400": request(b"GET /snapshot.json HTTP/1.0 extra\r\n\r\n")}
        self.path.write_text("{\"format\": \"laranex-schema\", \"version\": \"2.0\", \"secret_table\": 1")
        cases["503 invalid"] = get("/snapshot.json")
        self.path.write_text(json.dumps({**json.loads(valid), "version": "9.0"}))
        cases["503 version"] = get("/snapshot.json")
        self.path.unlink()
        cases["503 missing"] = get("/snapshot.json")
        for case, (head, body) in cases.items():
            with self.subTest(case):
                self.assertTrue(head.startswith(b"HTTP/1.0 " + case[:3].encode()), head)
                self.assertIn(b"\r\nCache-Control: no-store", head)
                self.assertIn(b"\r\nX-Content-Type-Options: nosniff", head)
                self.assertIn(f"\r\nContent-Security-Policy: {POLICY}".encode(), head)
                if not case.startswith("200"):
                    for name in [*names, b"secret_table", b"laranex-schema", str(self.path).encode()]:
                        self.assertNotIn(name, body)
                    self.assertIn(b"Content-Type: text/plain; charset=utf-8", head)
                    self.assertTrue(body.startswith(f"{case[:3]} ".encode()), body)
        self.assertEqual(cases["200"][1], valid)
        self.assertEqual(cases["503 missing"][1], b"503 Snapshot unavailable or invalid\n")

    def test_serve_binds_to_loopback_only_unless_explicitly_unsafe(self):
        from laranex_schema_viewer import cli
        from laranex_schema_viewer.server import is_loopback

        for host in ("127.0.0.1", "127.8.9.10", "::1", "localhost", "LOCALHOST"):
            self.assertTrue(is_loopback(host), host)
        for host in ("0.0.0.0", "", "::", "192.168.1.10", "10.0.0.1", "example.com", "localhost.example", "127.1"):
            self.assertFalse(is_loopback(host), host)
            with self.assertRaisesRegex(ValueError, "not a loopback address"):
                create_server(self.path, host=host, port=0)

        def serve(*args):
            stdout, stderr = io.StringIO(), io.StringIO()
            fake = MagicMock(server_port=8765)
            fake.__enter__.return_value = fake
            fake.serve_forever.side_effect = KeyboardInterrupt
            with patch("laranex_schema_viewer.server.create_server", return_value=fake) as created, \
                    patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                try:
                    code = cli.main(["serve", "--snapshot", str(self.path), *args])
                except SystemExit as exit:
                    code = exit.code
            return code, created, stdout.getvalue(), stderr.getvalue()

        code, created, out, err = serve()
        self.assertEqual(code, 0, err)
        self.assertEqual(created.call_args.args[1:3], ("127.0.0.1", 8765))
        self.assertEqual(created.call_args.kwargs, {"allow_remote": False})
        self.assertEqual(err, "")
        self.assertIn("http://127.0.0.1:8765", out)

        for host in ("0.0.0.0", "192.168.1.10", "example.com"):
            with self.subTest(refused=host):
                code, created, out, err = serve("--host", host)
                self.assertEqual(code, 2)
                created.assert_not_called()
                self.assertIn(f"--host {host} is not a loopback address", err)
                self.assertIn("--unsafe-allow-remote", err)

        code, created, out, err = serve("--host", "0.0.0.0", "--unsafe-allow-remote")
        self.assertEqual(code, 0, err)
        self.assertEqual(created.call_args.args[1:3], ("0.0.0.0", 8765))
        self.assertEqual(created.call_args.kwargs, {"allow_remote": True})
        for text in ("no authentication", "no TLS", "anyone who can reach 0.0.0.0:8765",
                     "Host checking is not access control", "authenticated HTTPS reverse proxy"):
            self.assertIn(text, err)
        code, created, out, err = serve("--unsafe-allow-remote")
        self.assertEqual((code, err), (0, ""))

    def test_serve_and_mcp_refuse_other_versions_clearly(self):
        rebuild = "rebuild it with `laranex-schema-viewer build`, which writes laranex-schema 2.0"
        cases = [({"format": "laranex-schema-viewer", "version": 1, "tables": []}, "is a version 1 snapshot", True),
                 ({"format": "laranex-schema", "version": "1.0"}, 'version "1.0"; this reader supports 2.x', True),
                 ({"format": "other"}, "is not a laranex-schema 2.x document", False)]
        for data, message, hint in cases:
            self.path.write_text(json.dumps(data))
            for command in ("serve", "mcp"):
                with self.subTest(command=command, message=message):
                    result = self.run_cli(command, "--snapshot", str(self.path))
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(message, result.stderr)
                    self.assertEqual(rebuild in result.stderr, hint)

    def test_http_loopback_end_to_end(self):
        try:
            server = create_server(self.path, port=0)
        except PermissionError:
            self.skipTest("Environment denies binding a loopback socket")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/snapshot.json", timeout=5) as response:
                self.assertEqual(len(json.load(response)["connections"][0]["tables"]), 6)
            with urlopen(base + "/", timeout=5) as response:
                self.assertIn(b'data-snapshot-url="snapshot.json"', response.read())
            with self.assertRaises(HTTPError):
                urlopen(base + "/../pyproject.toml", timeout=5)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_mcp_stdio_end_to_end(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters

        async def check():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "laranex_schema_viewer", "mcp", "--snapshot", str(self.path)],
                cwd=self.tmp.name,
                env={"PYTHONPATH": str(Path("src").resolve()), "PYTHONDONTWRITEBYTECODE": "1"},
            )
            async with Client(params, read_timeout_seconds=10) as client:
                tools = await client.list_tools()
                self.assertEqual(len(tools.tools), 6)
                self.assertTrue(all(t.annotations.read_only_hint for t in tools.tools))
                result = await client.call_tool("schema_overview", {"limit": 2})
                self.assertFalse(result.is_error)
                value = json.loads(result.content[0].text)
                self.assertEqual(value["counts"]["tables"], 6)
                self.assertEqual(len(value["items"]), 2)
                tid = value["items"][0]["id"]
                for name, args in [
                    ("schema_get_table", {"table": tid}),
                    ("schema_search", {"query_text": "User"}),
                    ("schema_get_model", {"model": example_document().models[0].id}),
                    ("schema_get_relationships", {"kind": "foreign_key"}),
                    ("schema_get_relationships", {"kind": "orm"}),
                    ("schema_compare", {"section": "differences"}),
                ]:
                    result = await client.call_tool(name, args)
                    self.assertFalse(result.is_error, result)
                result = await client.call_tool("schema_overview", {"limit": 0})
                self.assertTrue(result.is_error)
                result = await client.call_tool("schema_get_table", {"table": "../../.env"})
                self.assertTrue(result.is_error)

        asyncio.run(asyncio.wait_for(check(), timeout=30))

