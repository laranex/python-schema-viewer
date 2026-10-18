"""scripts/fetch_ui.py and the build hook's checks (hatch_build.py) against synthetic npm tarballs in a temporary root."""

import base64
import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hatch_build import OVERRIDE, STAMP, AssetError, check_assets, check_build

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_ui", ROOT / "scripts" / "fetch_ui.py")
fetch_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_ui)

NAME, VERSION = "laranex-schema-viewer-ui", "1.2.3"


def package_files(version=VERSION, **changes) -> dict[str, bytes]:
    """The members (below package/) of a minimal laranex-schema-viewer-ui tarball."""
    viewer = {"index.html": b"<!doctype html>", "app.js": b"console.log(1)", "assets/font.woff2": b"font"}
    manifest = {"name": NAME, "version": version, "contract": {"format": "laranex-schema", "major": 2},
                "files": [{"path": p, "size": len(c), "sha256": hashlib.sha256(c).hexdigest()}
                          for p, c in viewer.items()]}
    files = {"package.json": json.dumps({"name": NAME, "version": version}).encode(),
             "LICENSE": b"MIT", "NOTICE": b"notice",
             "dist/viewer/manifest.json": json.dumps(manifest).encode(),
             **{f"contract/{name}": b"{}" for name in fetch_ui.CONTRACT_FILES},
             "contract/check.mjs": b"// not installed"}
    files.update({f"dist/viewer/{p}": c for p, c in viewer.items()})
    files.update({f"contract/{d}/case.json": b"{}" for d in fetch_ui.CONTRACT_DIRECTORIES})
    files.update(changes)
    return files


def tarball(files: dict[str, bytes], extra=()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name if name.startswith("/") or name.startswith("other") else f"package/{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for info in extra:
            archive.addfile(info)
    return buffer.getvalue()


def integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class FetchUI(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def fetch(self, data: bytes, locked: bytes | None = None, allow_unverified=False):
        """Lock `locked` (default: data itself), then fetch data as a local tarball."""
        lock = {"name": NAME, "version": VERSION, "tarball": "https://registry.npmjs.org/x.tgz",
                "integrity": integrity(data if locked is None else locked)}
        (self.root / "ui.lock.json").write_text(json.dumps(lock))
        path = self.root / "ui.tgz"
        path.write_bytes(data)
        return fetch_ui.fetch(self.root, path, allow_unverified)

    def assert_nothing_installed(self):
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["ui.lock.json", "ui.tgz"])

    def test_installs_a_verified_tarball(self):
        stamp = self.fetch(tarball(package_files()))
        self.assertEqual((stamp["version"], stamp["verified"], stamp["source"]), (VERSION, True, "file:ui.tgz"))
        viewer = self.root / "src/laranex_schema_viewer/static/viewer"
        self.assertEqual(sorted(p.relative_to(viewer).as_posix() for p in viewer.rglob("*") if p.is_file()),
                         ["LICENSE.txt", "NOTICE.txt", "app.js", "assets/font.woff2", "index.html", "manifest.json"])
        self.assertTrue((self.root / "src/laranex_schema_viewer/contract/gate-report.schema.json").is_file())
        self.assertTrue((self.root / "tests/fixtures/contract/conformance/gate/case.json").is_file())
        self.assertFalse((self.root / "tests/fixtures/contract/check.mjs").exists())
        for target in ("sdist", "wheel"):
            check_build(self.root, target, "standard", environ={})

    def test_rejects_a_bad_hash_before_reading_the_tarball(self):
        data = tarball(package_files())
        with mock.patch.object(fetch_ui, "read_members") as read:
            with self.assertRaisesRegex(fetch_ui.FetchError, "does not match ui.lock.json"):
                self.fetch(data, locked=b"something else")
        read.assert_not_called()
        self.assert_nothing_installed()

    def test_rejects_unsafe_members(self):
        def link(kind, name="package/link"):
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, "../../etc/passwd"
            return info

        directory = tarfile.TarInfo("package/dist")
        directory.type = tarfile.DIRTYPE
        for case, data in (("traversal", tarball(package_files(**{"../evil.json": b"{}"}))),
                           ("dot segment", tarball(package_files(**{"dist/./x": b""})))
                           , ("absolute", tarball({**package_files(), "/etc/evil": b""})),
                           ("outside package/", tarball({**package_files(), "other/evil": b""})),
                           ("symlink", tarball(package_files(), [link(tarfile.SYMTYPE)])),
                           ("hardlink", tarball(package_files(), [link(tarfile.LNKTYPE)])),
                           ("directory", tarball(package_files(), [directory]))):
            with self.subTest(case):
                with self.assertRaisesRegex(fetch_ui.FetchError, "Unsafe tarball member"):
                    self.fetch(data)
                self.assert_nothing_installed()

    def test_rejects_another_version(self):
        with self.assertRaisesRegex(fetch_ui.FetchError, "pins laranex-schema-viewer-ui 1.2.3"):
            self.fetch(tarball(package_files(version="9.9.9")))
        self.assert_nothing_installed()

    def test_rejects_a_manifest_mismatch(self):
        with self.assertRaisesRegex(fetch_ui.FetchError, "app.js does not match the viewer manifest"):
            self.fetch(tarball(package_files(**{"dist/viewer/app.js": b"tampered"})))
        self.assert_nothing_installed()

    def test_rejects_missing_contract_cases(self):
        files = package_files()
        del files["contract/conformance/gate/case.json"]
        with self.assertRaisesRegex(fetch_ui.FetchError, "no contract/conformance/gate cases"):
            self.fetch(tarball(files))

    def test_an_unverified_local_tarball_blocks_release_builds(self):
        data = tarball(package_files())
        with self.assertRaisesRegex(fetch_ui.FetchError, "--allow-unverified"):
            self.fetch(data, locked=b"the published tarball")
        self.assert_nothing_installed()
        stamp = self.fetch(data, locked=b"the published tarball", allow_unverified=True)
        self.assertFalse(stamp["verified"])
        for target, version, environ in (("sdist", "standard", {OVERRIDE: "1"}), ("wheel", "standard", {OVERRIDE: "1"}),
                                         ("wheel", "editable", {})):
            with self.subTest(target=target, version=version, environ=environ):
                with self.assertRaisesRegex(AssetError, "unverified tarball"):
                    check_build(self.root, target, version, environ=environ)
        check_build(self.root, "wheel", "editable", environ={OVERRIDE: "1"})

    def test_the_build_checks_detect_changes_after_fetching(self):
        self.fetch(tarball(package_files()))
        app = self.root / "src/laranex_schema_viewer/static/viewer/app.js"
        app.write_text("changed")
        with self.assertRaisesRegex(AssetError, "app.js was changed after it was fetched"):
            check_assets(self.root)
        app.write_text("console.log(1)")
        check_assets(self.root)
        (self.root / "tests/fixtures/contract/extra.json").write_text("{}")
        with self.assertRaisesRegex(AssetError, "does not list"):
            check_assets(self.root)
        (self.root / STAMP).unlink()
        with self.assertRaisesRegex(AssetError, "run python scripts/fetch_ui.py"):
            check_assets(self.root)

    def test_builds_check_the_runtime_assets_without_the_test_fixtures(self):
        # An sdist carries the stamp and the runtime assets but not tests/fixtures/contract; its wheel must still build,
        # and every runtime asset must still be checked exactly.
        self.fetch(tarball(package_files()))
        fixtures = self.root / "tests/fixtures/contract"
        (fixtures / "extra.json").write_text("{}")
        (fixtures / "schema.json").write_text("changed")
        for target in ("sdist", "wheel"):
            check_build(self.root, target, "standard", environ={})
        with self.assertRaisesRegex(AssetError, "does not list"):
            check_assets(self.root)
        shutil.rmtree(self.root / "tests")
        for target in ("sdist", "wheel"):
            check_build(self.root, target, "standard", environ={})
        with self.assertRaisesRegex(AssetError, "does not list"):
            check_assets(self.root)
        viewer = self.root / "src/laranex_schema_viewer/static/viewer"
        schema = self.root / "src/laranex_schema_viewer/contract/gate-report.schema.json"
        for change, undo in (
                (lambda: (viewer / "extra.js").write_text(""), lambda: (viewer / "extra.js").unlink()),
                (lambda: (viewer / "app.js").write_text("changed"),
                 lambda: (viewer / "app.js").write_text("console.log(1)")),
                (lambda: schema.write_text("changed"), lambda: schema.write_text("{}")),
                (lambda: schema.unlink(), lambda: schema.write_text("{}"))):
            change()
            with self.assertRaises(AssetError):
                check_build(self.root, "wheel", "standard", environ={})
            undo()
            check_build(self.root, "wheel", "standard", environ={})

    def test_the_build_checks_follow_the_lock(self):
        self.fetch(tarball(package_files()))
        lock = json.loads((self.root / "ui.lock.json").read_text())
        (self.root / "ui.lock.json").write_text(json.dumps({**lock, "version": "1.2.4"}))
        with self.assertRaisesRegex(AssetError, "pins '1.2.4'"):
            check_assets(self.root)
        (self.root / "ui.lock.json").write_text(json.dumps({**lock, "integrity": integrity(b"other")}))
        with self.assertRaisesRegex(AssetError, "unverified"):
            check_assets(self.root)

    def test_the_build_hook_never_downloads(self):
        source = (ROOT / "hatch_build.py").read_text()
        for module in ("urllib", "http", "socket", "subprocess", "fetch_ui"):
            self.assertNotIn(f"import {module}", source)


if __name__ == "__main__":
    unittest.main()
