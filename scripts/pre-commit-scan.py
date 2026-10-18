#!/usr/bin/env python3
"""The pre-commit framework hook (.pre-commit-hooks.yaml, id laranex-schema-scan): `laranex-schema-viewer scan` from
this checkout's source, so the hook needs only Python 3.10+ and Git, not an installed package or the shared UI assets.
The scanner is standard library only."""

import sys
from pathlib import Path

if sys.version_info < (3, 10):
    print("laranex-schema-scan needs Python 3.10 or later as python3 on PATH", file=sys.stderr)
    sys.exit(2)  # A scan failure, never 1 (a confirmed exposure).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from laranex_schema_viewer.scan import main  # noqa: E402

sys.exit(main())
