"""Hatch build hook: refuse to build from shared UI assets that are missing, changed or not the ones ui.lock.json pins.

`python scripts/fetch_ui.py` installs the assets of the pinned laranex-schema-viewer-ui npm tarball and records them
in ui.stamp.json. This hook only reads files: it never downloads anything, so a wheel builds from the sdist (which
carries ui.lock.json, ui.stamp.json and the assets) offline, without Node.js.

Every file the stamp lists must exist with its recorded SHA-256, no other file may sit in the asset locations, the
stamp must name the locked package, version and integrity, and the viewer manifest must match its files. Assets from a
local tarball that does not match the lock are unverified: sdists and wheels are never built from them. Only an
editable install accepts them, and only with LSV_ALLOW_UNVERIFIED_UI=1.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # scripts/fetch_ui.py and the tests use check_assets without hatchling.
    BuildHookInterface = object

LOCK = "ui.lock.json"
STAMP = "ui.stamp.json"
VIEWER = "src/laranex_schema_viewer/static/viewer"
SCHEMAS = ("src/laranex_schema_viewer/contract/schema.json",
           "src/laranex_schema_viewer/contract/gate-report.schema.json")
FIXTURES = "tests/fixtures/contract"
CONTRACT_MAJOR = 2
OVERRIDE = "LSV_ALLOW_UNVERIFIED_UI"
FETCH = "run python scripts/fetch_ui.py"


class AssetError(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_files(root: Path) -> list[str]:
    """Every file in the asset locations, as POSIX paths relative to root."""
    found = [s for s in SCHEMAS if (root / s).exists()]
    for directory in (VIEWER, FIXTURES):
        found += [p.relative_to(root).as_posix() for p in (root / directory).rglob("*") if not p.is_dir()]
    return sorted(found)


def check_assets(root: Path, allow_unverified: bool = False) -> dict:
    """The stamp, after checking the installed assets against it, the lock and the viewer manifest."""
    try:
        lock = json.loads((root / LOCK).read_text())
        stamp = json.loads((root / STAMP).read_text())
    except (OSError, ValueError) as error:
        raise AssetError(f"The shared UI assets are missing or unreadable ({error}); {FETCH}") from None
    for key in ("name", "version"):
        if stamp.get(key) != lock[key]:
            raise AssetError(f"The installed UI assets are {key} {stamp.get(key)!r}, ui.lock.json pins "
                             f"{lock[key]!r}; {FETCH}")
    verified = stamp.get("verified") is True and stamp.get("integrity") == lock["integrity"]
    if not verified and not allow_unverified:
        raise AssetError(f"The installed UI assets come from an unverified tarball ({stamp.get('source')}); "
                         f"{FETCH} without --tarball, or with the locked tarball")
    files = stamp.get("files", {})
    if asset_files(root) != sorted(files):
        raise AssetError(f"The UI asset locations hold files ui.stamp.json does not list, or miss some; {FETCH}")
    for name, digest in files.items():
        if (root / name).is_symlink() or sha256(root / name) != digest:
            raise AssetError(f"{name} was changed after it was fetched; {FETCH}")
    viewer = root / VIEWER
    manifest = json.loads((viewer / "manifest.json").read_text())
    if (manifest.get("name"), manifest.get("version"), manifest.get("contract", {}).get("major")) != (
            lock["name"], lock["version"], CONTRACT_MAJOR):
        raise AssetError(f"The viewer manifest does not describe {lock['name']} {lock['version']} for "
                         f"laranex-schema {CONTRACT_MAJOR}.x; {FETCH}")
    for entry in manifest["files"]:
        path = viewer / entry["path"]
        if path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            raise AssetError(f"{entry['path']} does not match the viewer manifest; {FETCH}")
    return stamp


def check_build(root: Path, target: str, version: str, environ=os.environ) -> None:
    """Unverified assets reach only editable installs that ask for them; sdists and wheels never contain them."""
    editable = target == "wheel" and version == "editable"
    check_assets(root, allow_unverified=editable and environ.get(OVERRIDE) == "1")


class UIAssetsHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        try:
            check_build(Path(self.root), self.target_name, version)
        except AssetError as error:
            raise SystemExit(f"Cannot build laranex-schema-viewer: {error}") from None
