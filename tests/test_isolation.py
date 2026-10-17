"""Isolated extraction: parity with in-process builds, adversarial model modules, and failing closed."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from laranex_schema_viewer.isolation import ExtractRequest, IsolationConfig, IsolationError
from laranex_schema_viewer.isolation.env import worker_environment
from laranex_schema_viewer.isolation.runner import extract

ROOT = Path(__file__).resolve().parents[1]
ADVERSARIAL = "tests.fixtures.adversarial"


def installed(*packages):
    return all(importlib.util.find_spec(p) for p in packages)


def isolated(module, env=(), **config):
    request = ExtractRequest(project_root=str(ROOT), modules=(f"{ADVERSARIAL}.{module}",))
    return extract(request, IsolationConfig(env=tuple(env), **config))


def events(result):
    return {b["event"] for b in result.blocked}


def comment(result, table):
    return next(t["comment"] for t in result.document["tables"] if t["name"] == table)


class AdversarialModules(unittest.TestCase):
    def setUp(self):
        # Outside every directory the worker may write to.
        self.dir = Path(tempfile.mkdtemp(prefix="lsv-adversarial-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.file = self.dir / "written.txt"
        self.sqlite = self.dir / "app.sqlite3"

    def assert_extracted(self, result, prefix):
        names = {t["name"] for t in result.document["tables"]}
        self.assertEqual(names, {f"{prefix}_teams", f"{prefix}_members"})
        self.assertEqual(len(result.document["relationships"]), 1)

    def test_import_time_file_write_is_blocked(self):
        result = isolated("import_write", env=[("LSV_ADVERSARIAL_WRITE", str(self.file))])
        self.assertFalse(self.file.exists())
        self.assertIn("open", events(result))
        self.assert_extracted(result, "write")

    def test_sqlalchemy_ddl_never_reaches_the_database(self):
        result = isolated("create_all", env=[("LSV_ADVERSARIAL_SQLITE", str(self.sqlite))])
        self.assertFalse(self.sqlite.exists())
        self.assertIn("orm.connect", events(result))
        self.assert_extracted(result, "ddl")

    def test_raw_sqlite3_driver_is_blocked(self):
        result = isolated("raw_driver", env=[("LSV_ADVERSARIAL_SQLITE", str(self.sqlite))])
        self.assertFalse(self.sqlite.exists())
        self.assertIn("sqlite3.connect", events(result))
        self.assert_extracted(result, "raw")

    def test_mapper_callbacks_cannot_write(self):
        result = isolated("mapper_hook", env=[("LSV_ADVERSARIAL_WRITE", str(self.file))])
        self.assertFalse(self.file.exists())
        self.assertIn("open", events(result))
        self.assert_extracted(result, "hook")

    def test_network_connections_and_lookups_are_blocked(self):
        listener = socket.create_server(("127.0.0.1", 0))
        listener.settimeout(0.2)
        self.addCleanup(listener.close)
        accepted = []
        stop = threading.Event()

        def accept():
            while not stop.is_set():
                try:
                    accepted.append(listener.accept()[0])
                except OSError:
                    pass

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        host, port = listener.getsockname()
        try:
            result = isolated("network", env=[("LSV_ADVERSARIAL_TCP", f"{host}:{port}")])
        finally:
            stop.set()
            thread.join(2)
        self.assertEqual(accepted, [])
        outcome = json.loads(comment(result, "net_teams"))
        self.assertTrue(outcome["tcp"].startswith("blocked"), outcome)
        self.assertTrue(outcome["dns"].startswith("blocked"), outcome)
        self.assertTrue({"socket.connect", "socket.getaddrinfo"} <= events(result))

    def test_subprocesses_are_blocked(self):
        result = isolated("spawn", env=[("LSV_ADVERSARIAL_WRITE", str(self.file))])
        self.assertIn("subprocess.Popen", events(result))
        time.sleep(3)  # The child would have written by now.
        self.assertFalse(self.file.exists())
        self.assert_extracted(result, "spawn")

    def test_credentials_in_the_environment_and_home_are_not_visible(self):
        secrets = {"DATABASE_URL": "postgresql://app:hunter2@db/app", "LSV_ADVERSARIAL_SECRET": "s3cret",
                   "AWS_SECRET_ACCESS_KEY": "AKIA-not-real"}
        with mock.patch.dict(os.environ, secrets):
            result = isolated("secrets_probe")
        found = json.loads(comment(result, "secret_teams"))
        self.assertEqual(found["env"], dict.fromkeys(secrets))
        self.assertNotEqual(found["home"], str(Path.home()))
        self.assertFalse(found["pgpass"])
        self.assertNotIn("hunter2", json.dumps(result.document))

    def test_credential_names_cannot_be_passed_through(self):
        for name in ("DATABASE_URL", "DB_PASSWORD", "API_TOKEN", "SENTRY_DSN", "AWS_SECRET_ACCESS_KEY"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                worker_environment({name: "x"}, passthrough=(name,), fixed=(), home="/h", tmp="/t")

    def test_timeout_stops_the_worker(self):
        started = time.monotonic()
        with self.assertRaisesRegex(IsolationError, "within 2 seconds"):
            isolated("slow_import", timeout=2)
        self.assertLess(time.monotonic() - started, 30)

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX")
    def test_processes_that_evade_the_guards_are_killed_with_the_worker(self):
        # A libc fork raises no audit event: the guards miss it, and the process group cleanup must not.
        result = isolated("escape")
        pid = int(comment(result, "escape_teams"))
        self.assertGreater(pid, 0)
        self.assertNotIn("os.fork", events(result))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and alive(pid):
            time.sleep(0.1)
        self.assertFalse(alive(pid))

    def test_writes_outside_the_project_cannot_be_allowed(self):
        with self.assertRaisesRegex(IsolationError, "outside the project"):
            isolated("import_write", allow_writes=("../elsewhere",))

    def test_allowed_project_directories_are_writable(self):
        logs = ROOT / "tests" / ".lsv-allowed-writes"
        logs.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, logs, True)
        target = logs / "import.log"
        result = isolated("import_write", env=[("LSV_ADVERSARIAL_WRITE", str(target))],
                          allow_writes=("tests/.lsv-allowed-writes",))
        self.assertTrue(target.exists())
        self.assertNotIn("open", events(result))

    def test_failures_in_the_worker_surface(self):
        with self.assertRaisesRegex(IsolationError, "ModuleNotFoundError"):
            isolated("does_not_exist")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0; ps reports its state.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


PARITY = {
    "sqlalchemy": (("sqlalchemy",), dict(modules=("tests.fixtures.sqlalchemy_models",))),
    "sqlmodel": (("sqlmodel",), dict(modules=("tests.fixtures.sqlmodel_models",))),
    "peewee": (("peewee",), dict(modules=("tests.fixtures.peewee_models",))),
    "django": (("django",), dict(settings="tests.fixtures.django_settings", orm="django")),
    "tortoise": (("tortoise",), dict(targets=("tests.fixtures.tortoise_config:TORTOISE_ORM",), orm="tortoise")),
}


class OutputParity(unittest.TestCase):
    """Isolation must not change what is extracted, relationships included, for any supported ORM."""

    def test_isolated_output_matches_an_in_process_build(self):
        for orm, (packages, request) in PARITY.items():
            with self.subTest(orm=orm):
                if not installed(*packages):
                    self.skipTest(f"{orm} is not installed")
                result = extract(ExtractRequest(project_root=str(ROOT), **request), IsolationConfig())
                self.assertEqual(result.blocked, [])
                self.assertTrue(result.document["tables"])
                self.assertEqual(json.dumps(result.document, sort_keys=True), in_process(request))


def in_process(request):
    """A build in a fresh interpreter, as build_document asks, without isolation."""
    code = ("import json, sys; from pathlib import Path; from laranex_schema_viewer.build import build_document; "
            "r = json.loads(sys.argv[1]); "
            "d = build_document(r.get('modules', []), project_root=Path.cwd(), targets=r.get('targets', []), "
            "orm=r.get('orm', 'auto'), settings=r.get('settings')); "
            "print(json.dumps(d.model_dump(mode='json'), sort_keys=True))")
    return subprocess.run([sys.executable, "-B", "-c", code, json.dumps(request)], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


class FailingClosed(unittest.TestCase):
    def test_unknown_levels_are_refused(self):
        with self.assertRaisesRegex(IsolationError, "Unknown isolation level"):
            extract(ExtractRequest(project_root=str(ROOT)), IsolationConfig(level="none"))  # type: ignore[arg-type]

    def test_container_level_without_an_image_does_not_fall_back(self):
        with self.assertRaisesRegex(IsolationError, "container_image"):
            extract(ExtractRequest(project_root=str(ROOT)), IsolationConfig(level="container"))

    def test_container_level_without_a_runtime_does_not_fall_back(self):
        config = IsolationConfig(level="container", container_image="lsv-test", container_runtime="no-such-runtime")
        with self.assertRaisesRegex(IsolationError, "weaker isolation"):
            extract(ExtractRequest(project_root=str(ROOT)), config)


IMAGE = os.environ.get("LSV_TEST_CONTAINER_IMAGE")


@unittest.skipUnless(IMAGE, "set LSV_TEST_CONTAINER_IMAGE to an image with this package and SQLAlchemy installed")
class ContainerIsolation(unittest.TestCase):
    def config(self, **kw):
        return IsolationConfig(level="container", container_image=IMAGE, timeout=180, **kw)

    def test_matches_process_isolation(self):
        request = ExtractRequest(project_root=str(ROOT), modules=("tests.fixtures.sqlalchemy_models",))
        self.assertEqual(extract(request, self.config()).document, extract(request, IsolationConfig()).document)

    def test_has_no_network_and_a_read_only_project(self):
        request = ExtractRequest(project_root=str(ROOT), modules=(f"{ADVERSARIAL}.network",))
        result = extract(request, self.config(env=(("LSV_ADVERSARIAL_TCP", "1.1.1.1:53"),)))
        self.assertNotIn("succeeded", comment(result, "net_teams"))


if __name__ == "__main__":
    unittest.main()
