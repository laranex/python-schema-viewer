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
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from laranex_schema_viewer.server import create_server
from laranex_schema_viewer.snapshot import write_snapshot
from tests.test_extraction import example_document


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        write_snapshot(example_document(), self.path)

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
        # A version 1 snapshot reaches the viewer converted, as laranex-schema.
        body = json.loads(request("/snapshot.json").split(b"\r\n\r\n", 1)[1])
        self.assertEqual((body["format"], body["source"]), ("laranex-schema", "declared"))
        for route in ["/../pyproject.toml", "/%2e%2e/pyproject.toml", "/.env", "/favicon.ico", "/manifest.json",
                      "/LICENSE.txt", "/graph.js", "/styles.css", "/assets/", "/assets/../manifest.json"]:
            self.assertIn(b"404 Not Found", request(route))
        self.assertIn(b"403", request("/", host="untrusted.example"))
        self.assertIn(b"501", request("/snapshot.json", method="POST"))
        self.assertEqual(request("/snapshot.json", method="HEAD").split(b"\r\n\r\n")[1], b"")
        self.path.write_text("invalid")
        self.assertIn(b"503", request("/snapshot.json"))

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
                self.assertEqual(len(tools.tools), 5)
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
                ]:
                    result = await client.call_tool(name, args)
                    self.assertFalse(result.is_error, result)
                result = await client.call_tool("schema_overview", {"limit": 0})
                self.assertTrue(result.is_error)
                result = await client.call_tool("schema_get_table", {"table": "../../.env"})
                self.assertTrue(result.is_error)

        asyncio.run(asyncio.wait_for(check(), timeout=30))

