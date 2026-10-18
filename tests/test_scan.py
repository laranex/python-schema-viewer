"""Snapshot scanning: the port of the shared scanner (contract/scan.py) against every shared conformance case and the
shared JSON Schemas, the `scan` command on temporary Git repositories (as the UI's tests/scan.test.ts runs scan.mjs),
the repository's own allowlist, and the build's and init's Git protection of snapshots."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from laranex_schema_viewer import cli
from laranex_schema_viewer.config import GITIGNORE_COMMENT, add_gitignore, gitignore_pattern
from laranex_schema_viewer.contract import canonical_json
from laranex_schema_viewer.contract.scan import (SCAN_SIZE_LIMIT, ScanFile, ScanInput, allowlist_digest,
                                                 allowlist_json_schema, resolve_allowlist, scan_content, scan_files,
                                                 scan_report_json_schema, validate_allowlist, validate_scan_report)
from laranex_schema_viewer.scan import ScanOptions, output_status, run

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests" / "fixtures" / "contract"
CASES = sorted((CONTRACT / "conformance" / "scan").glob("*.json"))
SECRET = "secret_payroll_table"
GIT = shutil.which("git")

try:
    import jsonschema
except ImportError:  # Optional: the shapes reproduce the shared schemas exactly, and check every report themselves.
    jsonschema = None


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(schema_file: str, value) -> list[str]:
    """Errors of value against a shared JSON Schema, when the jsonschema package is installed (it comes with mcp)."""
    if jsonschema is None:
        return []
    validator = jsonschema.Draft202012Validator(load(CONTRACT / schema_file))
    return [error.message for error in validator.iter_errors(value)]


def run_case(case: dict) -> dict:
    """What every implementation does with a case: resolve the allowlist from the case's files, scan, report."""
    given = case["input"]

    def read(path: str):
        if path not in given["allowlists"]:
            raise KeyError(path)
        return given["allowlists"][path]

    allowlist = None if given["allowlist"] is None else resolve_allowlist(given["allowlist"], read)
    files = [ScanFile(f["scope"], f["path"], f["commit"],
                      base64.b64decode(f["contentBase64"]) if "contentBase64" in f else f.get("content", "").encode())
             for f in given["files"]]
    return scan_files(ScanInput(given["scopes"], files, allowlist, limit=given["limit"] or SCAN_SIZE_LIMIT))


def document_text(note: str | None = None) -> str:
    document = load(CONTRACT / "fixtures" / "valid" / "database-sqlite-indexes.json")
    return canonical_json({**document, "note": note})


class Conformance(unittest.TestCase):
    def test_every_shared_case(self):
        self.assertEqual(len(CASES), 45)
        for path in CASES:
            with self.subTest(case=path.name):
                case = load(path)
                self.assertEqual(validate_scan_report(case["expected"], strict=True), [])
                self.assertEqual(schema_errors("scan-report.schema.json", case["expected"]), [])
                report = run_case(case)
                self.assertEqual(report, case["expected"])
                self.assertEqual(validate_scan_report(report, strict=True), [])

    def test_cases_cover_every_exit_code_status_and_reason(self):
        reports = [load(path)["expected"] for path in CASES]
        findings = [f for r in reports for f in r["findings"]]
        self.assertEqual({r["exitCode"] for r in reports}, {0, 1, 2, 3})
        self.assertEqual({f["status"] for f in findings},
                         {"allowed", "confirmed", "incomplete", "moved", "stale", "tampered", "uncertain"})
        self.assertEqual({f["reason"] for f in findings},
                         {"document", "embedded_json", "laravel_envelope", "missing_connections", "nested_document",
                          "nested_legacy", "not_detected", "not_found", "object_literal", "size_limit",
                          "unparseable_json", "viewer_global", "viewer_v1"})

    def test_shapes_reproduce_the_shared_json_schemas(self):
        self.assertEqual(scan_report_json_schema(), load(CONTRACT / "scan-report.schema.json"))
        self.assertEqual(allowlist_json_schema(), load(CONTRACT / "allowlist.schema.json"))

    def test_report_rules_beyond_the_shape(self):
        report = load(CONTRACT / "conformance" / "scan" / "history-path-at-commit.json")["expected"]
        for change, message in (
                (lambda r: r.update(exitCode=0), "Exit code 0 does not follow"),
                (lambda r: r["counts"].update(allowed=0), "Counts do not count"),
                (lambda r: r["findings"].reverse(), "Findings are not sorted"),
                (lambda r: r["findings"][1].update(commit="1" * 40), "has a commit exactly when"),
                (lambda r: r["findings"][1].update(scope="allowlist"), "Only a stale allowlist entry"),
                (lambda r: r.update(version="1.1"), "strict validation is for version 1.0")):
            changed = json.loads(json.dumps(report))
            change(changed)
            self.assertTrue(any(message in e for e in validate_scan_report(changed, strict=True)), message)
        self.assertEqual(validate_scan_report({**report, "version": "1.1"}), [])
        self.assertIn("$: unknown property extra", validate_scan_report({**report, "extra": 1}, strict=True))


class ScanContent(unittest.TestCase):
    doc = document_text().encode()

    def test_classifies_by_content_not_by_file_name(self):
        self.assertEqual(scan_content("anything.json", self.doc), {"kind": "document", "reason": "document"})
        self.assertEqual(scan_content("ANY.JSON", self.doc), {"kind": "document", "reason": "document"})
        self.assertIsNone(scan_content("schema-viewer.json", b'{"name":"schema-viewer"}'))

    def test_reads_javascript_and_source_maps_only_in_artifacts(self):
        literal = b'const d={format:"laranex-schema",version:"2.0",connections:[]};'
        for ext in (".js", ".mjs", ".cjs", ".map"):
            self.assertIsNone(scan_content(f"a{ext}", literal, mode="tree"), ext)
            self.assertEqual(scan_content(f"a{ext}", literal, mode="artifact")["kind"], "embedded", ext)
        for path in ("a.ts", "a.md", "a.txt", "a.svg", "json", ".json"):
            self.assertIsNone(scan_content(path, self.doc, mode="artifact"), path)

    def test_a_file_over_the_limit_is_too_large(self):
        self.assertEqual(scan_content("a.json", self.doc, limit=10), {"kind": "too_large", "reason": "size_limit"})

    def test_does_not_decode_encoded_payloads(self):
        encoded = base64.b64encode(self.doc).decode()
        page = f'<script type="application/octet-stream" id="schema-viewer-config">{encoded}</script>'
        self.assertIsNone(scan_content("export.html", page.encode()))

    def test_reads_json_as_javascript_does(self):
        document = '{"format":"laranex-schema","version":"2.0","connections":[]%s}'
        # NaN is not JSON: the file does not parse, and holds the format pair.
        self.assertEqual(scan_content("a.json", (document % ',"x":NaN').encode())["reason"], "unparseable_json")
        self.assertEqual(scan_content("a.json", (document % (',"x":' + "9" * 5000)).encode())["kind"], "document")
        self.assertEqual(scan_content("a.json", b"\xef\xbb\xbf\xef\xbb\xbf" + (document % "").encode())["kind"],
                         "document")
        self.assertIsNone(scan_content("a.json", b'{"format":"laranex-schema","version":"\xd9\xa2.0","connections":[]}'))
        self.assertEqual(scan_content("a.json", b'{"format":2.0,"document":{"connections":[]}}')["reason"],
                         "laravel_envelope")

    def test_never_raises_on_arbitrary_bytes(self):
        for data in (b"\xff\xfe\x00{", b"", b'{"format":"laranex-schema"', b"[" * 18 + b"]" * 18,
                     b"[" * 100000 + b'{"format":"laranex-schema"}' + b"]" * 100000):
            for path in ("x.json", "x.html"):
                scan_content(path, data)


class Allowlists(unittest.TestCase):
    entry = {"path": "a/b.json", "sha256": "0" * 64}

    def allowlist(self, entries, **extra):
        return {"format": "laranex-schema-allowlist", "version": "1.0", "entries": entries, **extra}

    def test_digest_reads_crlf_as_lf(self):
        self.assertEqual(allowlist_digest(b"a\nb\r\nc\r"), hashlib.sha256(b"a\nb\nc\r").hexdigest())
        self.assertEqual(allowlist_digest(b"\r\r\n"), hashlib.sha256(b"\r\n").hexdigest())
        self.assertEqual(allowlist_digest(b""), hashlib.sha256(b"").hexdigest())

    def test_shipped_allowlists_are_valid(self):
        for path in (CONTRACT / "synthetic.json", ROOT / ".laranex-schema-allowlist.json"):
            self.assertEqual(validate_allowlist(load(path)), [], path)
            self.assertEqual(schema_errors("allowlist.schema.json", load(path)), [], path)

    def test_rejects_anything_but_plain_relative_paths_digests_and_known_properties(self):
        for path in ("/abs.json", "../up.json", "a/../b.json", "./a.json", "a//b.json", "a\\b.json", "dir/", "",
                     "a\x00.json"):
            value = self.allowlist([{**self.entry, "path": path}])
            self.assertNotEqual(validate_allowlist(value), [], repr(path))
        for value in (self.allowlist([{**self.entry, "sha256": "ABC"}]), self.allowlist([self.entry], extra=True),
                      {**self.allowlist([self.entry]), "version": "1.1"}, self.allowlist([self.entry, self.entry]),
                      self.allowlist([], include=[{"path": "x.json", "root": "/"}])):
            self.assertNotEqual(validate_allowlist(value), [], value)
        self.assertEqual(validate_allowlist(self.allowlist(
            [self.entry, {**self.entry, "sha256": "1" * 64}], include=[{"path": ".hidden/x.json", "root": "."}])), [])
        self.assertEqual(validate_allowlist(self.allowlist([self.entry, self.entry])),
                         ["$.entries[1]: a/b.json with this sha256 is listed more than once"])

    def test_roots_of_nested_includes_compose(self):
        files = {
            "a.json": self.allowlist([], include=[{"path": "b/b.json", "root": "one"}]),
            "b/b.json": self.allowlist([{"path": "x.json", "sha256": "0" * 64}],
                                       include=[{"path": "c.json", "root": "two"}]),
            "b/c.json": self.allowlist([{"path": "y.json", "sha256": "0" * 64}]),
        }
        resolved = resolve_allowlist("a.json", files.__getitem__)
        self.assertEqual(resolved.entries, [{"path": "one/two/y.json", "sha256": "0" * 64},
                                            {"path": "one/x.json", "sha256": "0" * 64}])
        self.assertEqual(resolved.errors, [])

    def test_the_repository_allowlist_is_current(self):
        spec = importlib.util.spec_from_file_location("write_allowlist", ROOT / "scripts" / "write_allowlist.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual((ROOT / ".laranex-schema-allowlist.json").read_text(encoding="utf-8"), module.allowlist(),
                         "run python scripts/write_allowlist.py")


@unittest.skipUnless(GIT, "git is not installed")
class ScanCommand(unittest.TestCase):
    """The command on temporary Git repositories, in process."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(os.path.realpath(directory.name))
        self.count = 0
        self.document = document_text(SECRET)
        self.fragment = f'<pre>{{"format":"laranex-schema","version":"2.0","note":"{SECRET}"}}</pre>\n'

    def git(self, directory: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(directory), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                               "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                              text=True).stdout

    def write(self, directory: Path, files: dict[str, str]) -> None:
        for path, content in files.items():
            (directory / path).parent.mkdir(parents=True, exist_ok=True)
            (directory / path).write_text(content, encoding="utf-8")

    def repo(self, files: dict[str, str] | None = None) -> Path:
        directory = self.work / f"repo-{self.count}"
        self.count += 1
        directory.mkdir()
        self.git(directory, "init", "-q", "-b", "main")
        self.write(directory, files or {})
        return directory

    def commit(self, directory: Path) -> str:
        self.git(directory, "add", "-A")
        self.git(directory, "commit", "-q", "--allow-empty", "-m", "c")
        return self.git(directory, "rev-parse", "HEAD").strip()

    def scan(self, cwd: Path, limit: int = SCAN_SIZE_LIMIT, **flags):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run(ScanOptions(json=True, **flags), cwd=str(cwd), stdout=stdout, stderr=stderr, limit=limit)
        report = json.loads(stdout.getvalue())
        self.assertEqual(validate_scan_report(report, strict=True), [])
        self.assertEqual(schema_errors("scan-report.schema.json", report), [])
        self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(report["exitCode"], code)
        return code, report

    @staticmethod
    def summary(report) -> list[str]:
        return [f"{f['scope']} {f['status']} {f['path']}" + (f"@{f['commit'][:7]}" if f["commit"] else "")
                for f in report["findings"]]

    def digest(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def allow(self, entries, include=None) -> str:
        value = {"format": "laranex-schema-allowlist", "version": "1.0",
                 "entries": [{"path": p, "sha256": self.digest(t)} for p, t in entries]}
        return json.dumps({**value, **({"include": include} if include else {})})

    def test_finds_a_tracked_snapshot_also_one_tracked_before_it_was_ignored(self):
        directory = self.repo({"build/schema-viewer.json": self.document, "package.json": '{"name":"x"}'})
        self.commit(directory)
        self.write(directory, {".gitignore": "build/\n"})
        self.commit(directory)
        code, report = self.scan(directory, tracked=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.summary(report), ["tracked confirmed build/schema-viewer.json"])
        self.assertEqual({k: report["findings"][0][k] for k in ("kind", "reason", "commit", "sha256")},
                         {"kind": "document", "reason": "document", "commit": None,
                          "sha256": self.digest(self.document)})
        self.assertEqual(report["counts"]["files"], 2)

    def test_reads_the_staged_bytes_not_the_working_tree(self):
        directory = self.repo({"a.json": self.document, "b.json": "{}"})
        self.git(directory, "add", "-A")
        self.write(directory, {"a.json": "{}", "b.json": self.document})
        self.assertEqual(self.summary(self.scan(directory, tracked=True)[1]), ["tracked confirmed a.json"])

    def test_untracked_files_git_would_add_and_ignored_ones_only_as_artifacts(self):
        bundle = f"const d=JSON.parse({json.dumps(json.dumps(load(CONTRACT / 'fixtures/valid/database-sqlite-indexes.json')))});"
        directory = self.repo({".gitignore": "/dist/\n/ignored.json\n", "new.json": self.document,
                               "ignored.json": self.document, "dist/app.js": bundle})
        self.commit(directory)
        self.write(directory, {"later.json": self.document})
        code, report = self.scan(directory, untracked=True)
        self.assertEqual((code, self.summary(report)), (1, ["untracked confirmed later.json"]))
        self.assertEqual(self.summary(self.scan(directory, tracked=True, untracked=True, artifacts=["dist"])[1]),
                         ["artifacts confirmed dist/app.js", "untracked confirmed later.json",
                          "tracked confirmed new.json"])

    def test_artifacts_without_ignore_rules_from_any_directory_with_or_without_git(self):
        plain = self.work / "plain"
        page = f"<script>window.__SCHEMA_VIEWER__ = {json.dumps({'document': json.loads(self.document)})};</script>"
        self.write(plain, {"out/deep/page.html": page, "out/app.js.map": "{}"})
        code, report = self.scan(plain, artifacts=["out"])
        self.assertEqual(code, 1)
        self.assertEqual([(f["path"], f["reason"]) for f in report["findings"]], [("out/deep/page.html", "viewer_global")])
        self.assertEqual([f["path"] for f in self.scan(plain / "out", artifacts=["."])[1]["findings"]],
                         ["deep/page.html"])
        code, report = self.scan(plain, artifacts=["missing"])
        self.assertEqual((code, report["errors"]), (2, [{"kind": "artifact_missing",
                                                         "message": "The artifact directory missing does not exist."}]))

    def test_a_symbolic_link_to_a_directory_is_not_followed(self):
        directory = self.work / "links"
        self.write(directory, {"target/a.json": "{}", "out/x.json": "{}"})
        (directory / "out" / "linked").symlink_to(directory / "target")
        code, report = self.scan(directory, artifacts=["out"])
        self.assertEqual(code, 2)
        self.assertEqual(report["findings"], [{"scope": "artifacts", "status": "incomplete", "kind": "unreadable",
                                               "path": "out/linked", "commit": None, "sha256": None,
                                               "reason": "symlinked_directory"}])

    def test_history_on_request_naming_the_commit_and_never_on_a_shallow_clone(self):
        directory = self.repo({"old.json": self.document})
        first = self.commit(directory)
        (directory / "old.json").unlink()
        self.commit(directory)
        self.assertEqual(self.scan(directory, tracked=True)[0], 0)
        code, report = self.scan(directory, history=True)
        self.assertEqual(code, 1)
        self.assertEqual(report["findings"], [{"scope": "history", "status": "confirmed", "kind": "document",
                                               "path": "old.json", "commit": first,
                                               "sha256": self.digest(self.document), "reason": "document"}])
        shallow = self.work / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{directory}", str(shallow)], check=True,
                       capture_output=True)
        code, report = self.scan(shallow, history=True)
        self.assertEqual((code, [e["kind"] for e in report["errors"]]), (2, ["history_unavailable"]))

    def test_history_finds_blobs_only_a_branch_holds(self):
        directory = self.repo({"a.json": "{}"})
        self.commit(directory)
        self.git(directory, "checkout", "-q", "-b", "side")
        self.write(directory, {"side.json": self.document})
        side = self.commit(directory)
        self.git(directory, "checkout", "-q", "main")
        self.assertEqual(self.summary(self.scan(directory, history=True)[1]), [f"history confirmed side.json@{side[:7]}"])

    def test_allowlist_allowed_tampered_moved_and_stale(self):
        allowlist = self.allow([("fixtures/a.json", self.document), ("fixtures/gone.json", self.document)])
        directory = self.repo({"fixtures/a.json": self.document, ".laranex-schema-allowlist.json": allowlist})
        self.commit(directory)
        code, report = self.scan(directory, tracked=True, untracked=True)
        self.assertEqual((code, self.summary(report)),
                         (0, ["tracked allowed fixtures/a.json", "allowlist stale fixtures/gone.json"]))
        self.write(directory, {"fixtures/a.json": self.document.replace("sqlite", "mysql")})
        self.assertEqual(self.summary(self.scan(directory, tracked=True)[1]), ["tracked allowed fixtures/a.json"])
        self.git(directory, "add", "-A")
        code, report = self.scan(directory, tracked=True)
        self.assertEqual((code, report["counts"]["tampered"]), (1, 1))
        self.git(directory, "mv", "fixtures/a.json", "b.json")
        self.git(directory, "checkout", "HEAD", "--", "fixtures/a.json")
        self.write(directory, {"b.json": self.document})
        self.git(directory, "add", "-A")
        self.assertEqual(self.summary(self.scan(directory, tracked=True)[1]),
                         ["tracked moved b.json", "tracked allowed fixtures/a.json"])

    def test_another_allowlist_with_includes_under_a_root_and_invalid_ones_fail(self):
        directory = self.repo({
            "vendor/pkg/contract/fixtures/x.json": self.document,
            "ci/allow.json": self.allow([], [{"path": "../vendor/pkg/contract/synthetic.json", "root": "vendor/pkg"}]),
            "ci/vendor.json": self.allow([], [{"path": "pkg/synthetic.json", "root": "vendor/pkg"}]),
            "ci/pkg/synthetic.json": self.allow([("contract/fixtures/x.json", self.document)]),
            "broken.json": '{"format":"laranex-schema-allowlist"}',
        })
        self.commit(directory)
        self.assertEqual(self.scan(directory, tracked=True, allowlist="ci/vendor.json")[0], 0)
        self.assertEqual([e["kind"] for e in self.scan(directory, tracked=True, allowlist="ci/allow.json")[1]["errors"]],
                         ["allowlist_invalid"])
        code, report = self.scan(directory, tracked=True, allowlist="broken.json")
        self.assertEqual((code, report["counts"]["confirmed"]), (2, 1))
        self.assertEqual(self.scan(directory, tracked=True, allowlist="nowhere.json")[0], 2)

    def test_exit_precedence_uncertain_confirmed_incomplete(self):
        directory = self.repo({"docs/page.html": self.fragment})
        self.commit(directory)
        self.assertEqual(self.scan(directory, tracked=True)[0], 3)
        self.write(directory, {"b.json": self.document})
        self.assertEqual(self.scan(directory, tracked=True, untracked=True)[0], 1)
        code, report = self.scan(directory, limit=50, tracked=True, untracked=True)
        self.assertEqual(code, 2)
        self.assertEqual(self.summary(report), ["untracked incomplete b.json", "tracked incomplete docs/page.html"])
        self.assertTrue(all(f["kind"] == "too_large" and f["sha256"] is None for f in report["findings"]))

    def test_not_a_repository(self):
        plain = self.work / "plain"
        plain.mkdir()
        code, report = self.scan(plain, tracked=True)
        self.assertEqual((code, [e["kind"] for e in report["errors"]]), (2, ["not_a_repository"]))

    def test_this_repository_with_its_allowlist(self):
        code, report = self.scan(ROOT, tracked=True)
        self.assertEqual([s for s in self.summary(report) if not s.startswith("tracked allowed")], [])
        self.assertEqual(code, 0)


@unittest.skipUnless(GIT, "git is not installed")
class ScanCli(unittest.TestCase):
    """The installed command: usage errors, the summary line and the report file, never any content."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)

    def run_cli(self, *args, cwd):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
        return subprocess.run([sys.executable, "-m", "laranex_schema_viewer", "scan", *args], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_usage_errors_exit_2_and_the_summary_prints_counts_only(self):
        for args in ([], ["--bogus"], ["--artifacts"], ["--artifacts", "--tracked"], ["--track"]):
            result = self.run_cli(*args, cwd=self.work)
            self.assertEqual((result.returncode, result.stdout), (2, ""), args)
        repo = self.work / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "a.json").write_text(document_text(SECRET), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "a.json"], check=True)
        result = self.run_cli("--tracked", "--report", "../report.json", cwd=repo)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "laranex-schema-scan: 1 files (tracked); 1 confirmed, 0 tampered, 0 moved, "
                                        "0 uncertain, 0 incomplete, 0 allowed, 0 stale; exit 1\n")
        report = (self.work / "report.json").read_text(encoding="utf-8")
        self.assertEqual([f["path"] for f in json.loads(report)["findings"]], ["a.json"])
        result = self.run_cli("--tracked", cwd=repo)
        self.assertEqual(result.stdout.splitlines()[1], "Paths and reasons are in the report: --report FILE or --json.")
        for text in (report, result.stdout, result.stderr):
            self.assertNotIn(SECRET, text)
        result = self.run_cli("--tracked", cwd=self.work)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not_a_repository", result.stderr)

    def test_the_pre_commit_hook_runs_the_command_from_source(self):
        hooks = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
        self.assertIn("- id: laranex-schema-scan\n", hooks)
        self.assertIn("  entry: scripts/pre-commit-scan.py\n  language: script\n  args: [--tracked]\n", hooks)
        script = ROOT / "scripts" / "pre-commit-scan.py"
        self.assertTrue(os.access(script, os.X_OK))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run([sys.executable, "-I", str(script), "--tracked"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("laranex-schema-scan: "))


@unittest.skipUnless(GIT, "git is not installed")
class SnapshotProtection(unittest.TestCase):
    """build refuses a tracked output and warns about an unignored one; init ignores the snapshot paths."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(os.path.realpath(directory.name))

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def build(self, output: str):
        models = self.root / "app" / "models.py"
        if not models.is_file():
            models.parent.mkdir()
            (models.parent / "__init__.py").write_text("")
            models.write_text("from sqlalchemy import Column, Integer\nfrom sqlalchemy.orm import declarative_base\n"
                              "Base = declarative_base()\nclass Item(Base):\n    __tablename__ = 'items'\n"
                              "    id = Column(Integer, primary_key=True)\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["build", "--project-root", str(self.root), "--module", "app.models",
                             "--output", str(self.root / output)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_output_status(self):
        self.assertIsNone(output_status(self.root / "schema-viewer.json"))
        self.git("init", "-q")
        (self.root / ".gitignore").write_text("/ignored/\n")
        self.assertEqual(output_status(self.root / "schema-viewer.json"), "unignored")
        self.assertEqual(output_status(self.root / "new" / "deep" / "schema.json"), "unignored")
        self.assertIsNone(output_status(self.root / "ignored" / "schema.json"))
        (self.root / "[x].json").write_text("{}")
        (self.root / "x.json").write_text("{}")
        self.git("--literal-pathspecs", "add", "-f", "[x].json")
        self.assertEqual(output_status(self.root / "[x].json"), "tracked")
        self.assertEqual(output_status(self.root / "x.json"), "unignored")

    def test_build_fails_before_writing_a_tracked_output_and_warns_about_an_unignored_one(self):
        code, _, stderr = self.build("schema-viewer.json")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("Warning", stderr)
        self.git("init", "-q")
        code, _, stderr = self.build("schema-viewer.json")
        self.assertEqual(code, 0, stderr)
        self.assertIn("is in a Git work tree and not ignored", stderr)
        self.git("add", "-f", "schema-viewer.json")
        before = (self.root / "schema-viewer.json").read_bytes()
        (self.root / ".gitignore").write_text("/schema-viewer.json\n")
        code, stdout, stderr = self.build("schema-viewer.json")
        self.assertEqual(code, 1)
        self.assertIn("is tracked by Git", stderr)
        self.assertIn("git rm --cached -- ", stderr)
        self.assertNotIn("Extracted", stdout)
        self.assertEqual((self.root / "schema-viewer.json").read_bytes(), before)
        self.git("rm", "-q", "--cached", "schema-viewer.json")
        code, _, stderr = self.build("schema-viewer.json")
        self.assertEqual((code, "Warning" in stderr), (0, False))

    def test_init_appends_an_idempotent_anchored_block(self):
        (self.root / ".gitignore").write_text("node_modules/\n/schema-viewer.html")
        self.assertEqual(add_gitignore(self.root, "out/schema.json"), ["/schema-viewer.json", "/out/schema.json"])
        text = (self.root / ".gitignore").read_text()
        self.assertEqual(text, f"node_modules/\n/schema-viewer.html\n\n{GITIGNORE_COMMENT}\n/schema-viewer.json\n"
                               "/out/schema.json\n")
        self.assertEqual(add_gitignore(self.root, "out/schema.json"), [])
        self.assertEqual((self.root / ".gitignore").read_text(), text)
        self.assertEqual(add_gitignore(self.root, "schema-viewer.json"), [])
        self.assertEqual(gitignore_pattern(self.root, "./a/../b [1].json"), "/b \\[1].json")
        self.assertEqual(gitignore_pattern(self.root, str(self.root / "x.json")), "/x.json")
        self.assertIsNone(gitignore_pattern(self.root, "../outside.json"))
        self.assertIsNone(gitignore_pattern(self.root, "/elsewhere/x.json"))

    def test_init_reports_what_it_added(self):
        (self.root / "app" / "models").mkdir(parents=True)
        (self.root / "app" / "__init__.py").write_text("")
        (self.root / "app" / "models" / "__init__.py").write_text("")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "src"))
        command = [sys.executable, "-m", "laranex_schema_viewer", "init", "--yes"]
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Added /schema-viewer.json, /schema-viewer.html to", result.stdout)
        self.assertEqual((self.root / ".gitignore").read_text(),
                         f"{GITIGNORE_COMMENT}\n/schema-viewer.json\n/schema-viewer.html\n")


if __name__ == "__main__":
    unittest.main()
