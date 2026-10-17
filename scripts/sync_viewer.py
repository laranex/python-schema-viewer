"""Vendor a laranex-schema-viewer-ui standalone build (dist/viewer) into the package.

    python scripts/sync_viewer.py /path/to/schema-viewer-ui

Copies exactly the files the build's manifest.json lists, after checking each one's size and SHA-256, plus the UI's
LICENSE and NOTICE. Replaces src/laranex_schema_viewer/static/viewer/ entirely. Node.js is needed only to build the
UI, never by this package at runtime.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/laranex_schema_viewer/static/viewer"
CONTRACT_MAJOR = 2


def verify(build: Path) -> dict:
    manifest = json.loads((build / "manifest.json").read_text())
    if manifest.get("name") != "laranex-schema-viewer-ui":
        raise SystemExit(f"{build} is not a laranex-schema-viewer-ui build")
    if manifest.get("contract", {}).get("major") != CONTRACT_MAJOR:
        raise SystemExit(f"The build reads laranex-schema {manifest.get('contract')}; this package writes 2.x")
    for entry in manifest["files"]:
        path = (build / entry["path"]).resolve()
        if build.resolve() not in path.parents:
            raise SystemExit(f"Manifest path escapes the build: {entry['path']}")
        data = path.read_bytes()
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SystemExit(f"{entry['path']} does not match the manifest; rebuild the UI")
    return manifest


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    ui = Path(sys.argv[1]).resolve()
    build = ui / "dist/viewer"
    manifest = verify(build)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    for entry in manifest["files"]:
        destination = TARGET / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(build / entry["path"], destination)
    shutil.copyfile(build / "manifest.json", TARGET / "manifest.json")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ui / name, TARGET / f"{name}.txt")
    verify(TARGET)
    print(f"Vendored laranex-schema-viewer-ui {manifest['version']}: {len(manifest['files'])} files")


if __name__ == "__main__":
    main()
