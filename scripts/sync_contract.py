"""Vendor the shared laranex-schema contract fixtures from a laranex-schema-viewer-ui checkout.

    python scripts/sync_contract.py /path/to/schema-viewer-ui

Copies contract/schema.json and contract/gate-report.schema.json, the valid and invalid documents
(contract/fixtures/{valid,invalid}), the valid and invalid gate reports (contract/fixtures/gate/{valid,invalid}) and the
conformance cases (contract/conformance/{graph,comparison,migrations,inference,gate}) into tests/fixtures/contract/,
replacing what was there, and records the source commit, the UI version and the SHA-256 of every file in
manifest.json. The package's distributed schemas (src/laranex_schema_viewer/contract/schema.json and
gate-report.schema.json) are the same files. tests/test_contract.py fails when a vendored file no longer matches the
manifest, so never edit the copies: change the UI repository and sync.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests/fixtures/contract"
PACKAGE = ROOT / "src/laranex_schema_viewer/contract"
SCHEMAS = ("schema.json", "gate-report.schema.json")
DIRECTORIES = ("fixtures/valid", "fixtures/invalid", "fixtures/gate/valid", "fixtures/gate/invalid",
               "conformance/graph", "conformance/comparison", "conformance/migrations", "conformance/inference",
               "conformance/gate")


def git(ui: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ui), *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    ui = Path(sys.argv[1]).resolve()
    contract = ui / "contract"
    package = json.loads((ui / "package.json").read_text())
    if package.get("name") != "laranex-schema-viewer-ui" or not all((contract / s).is_file() for s in SCHEMAS):
        raise SystemExit(f"{ui} is not a laranex-schema-viewer-ui checkout")
    if git(ui, "status", "--porcelain", "--", "contract"):
        raise SystemExit(f"{contract} has uncommitted changes; vendor a committed state")
    files = [*SCHEMAS] + sorted(str(p.relative_to(contract)) for d in DIRECTORIES
                                     for p in (contract / d).glob("*.json"))
    if TARGET.exists():
        shutil.rmtree(TARGET)
    for name in files:
        (TARGET / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contract / name, TARGET / name)
    for name in SCHEMAS:
        shutil.copyfile(contract / name, PACKAGE / name)
    manifest = {
        "source": "laranex-schema-viewer-ui",
        "commit": git(ui, "rev-parse", "HEAD"),
        "version": package["version"],
        "files": {name: hashlib.sha256((TARGET / name).read_bytes()).hexdigest() for name in files},
    }
    (TARGET / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Vendored the laranex-schema contract from laranex-schema-viewer-ui {manifest['version']} "
          f"({manifest['commit'][:7]}): {len(files)} files")


if __name__ == "__main__":
    main()
