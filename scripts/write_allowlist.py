"""Write .laranex-schema-allowlist.json: the synthetic documents this repository holds and publishes, by path and digest.

    PYTHONPATH=src python scripts/write_allowlist.py          # rewrite the allowlist
    PYTHONPATH=src python scripts/write_allowlist.py --check  # fail if it is not current

Only the directories listed here are allowlisted, never whatever a scan happens to find: in each, the files the scanner
detects (the synthetic documentation demo and the two MCP recordings that embed synthetic documents), and the demo's
published copy in the documentation build. Run it after changing one of them, for example after
scripts/record_mcp_conformance.py; tests/test_scan.py checks that the allowlist is current.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".laranex-schema-allowlist.json"
# Directories of reviewed synthetic documents.
SYNTHETIC_DIRECTORIES = (".gh-page/demo", "tests/fixtures/mcp-conformance")
# Published copies, by artifact path (as `scan --artifacts .gh-page/.vitepress/dist` names it), of a listed source file:
# .gh-page/scripts/prepare-demo.mjs serves the demo document as demo/snapshot.json, byte for byte.
PUBLISHED_COPIES = {".gh-page/.vitepress/dist/demo/snapshot.json": ".gh-page/demo/schema.json"}


def allowlist(root: Path = ROOT) -> str:
    from laranex_schema_viewer.contract import canonical_json
    from laranex_schema_viewer.contract.scan import (ALLOWLIST_FORMAT, ALLOWLIST_VERSION, allowlist_digest,
                                                     scan_content)

    digests = {}
    for directory in SYNTHETIC_DIRECTORIES:
        for path in sorted((root / directory).iterdir()):
            name = path.relative_to(root).as_posix()
            if path.is_file() and scan_content(name, path.read_bytes()):
                digests[name] = allowlist_digest(path.read_bytes())
    for copy, source in PUBLISHED_COPIES.items():
        digests[copy] = digests[source]
    entries = [{"path": path, "sha256": digest} for path, digest in sorted(digests.items())]
    return canonical_json({"format": ALLOWLIST_FORMAT, "version": ALLOWLIST_VERSION, "entries": entries})


def main() -> None:
    text = allowlist()
    if "--check" in sys.argv:
        if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != text:
            raise SystemExit(f"{TARGET.name} is not current: run python scripts/write_allowlist.py")
        print(f"{TARGET.name} is current")
        return
    TARGET.write_text(text, encoding="utf-8")
    print(f"Wrote {TARGET.name}: {text.count('sha256')} entries")


if __name__ == "__main__":
    main()
