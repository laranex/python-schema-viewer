"""MCP responses for version 1 snapshots stay exactly what they were before laranex-schema support.

tests/fixtures/mcp/v1-responses.json was recorded from the query service as it was before it learned the unified
contract (commit 18fc199). Regenerate it only for an intended, documented response change:
    python -m tests.test_mcp_compatibility --record
"""

import json
import sys
import unittest
from pathlib import Path

from laranex_schema_viewer.query import SnapshotQuery

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ["tests/fixtures/v1/shop.json", "tests/fixtures/v1/sqlalchemy.json"]
GOLDEN = ROOT / "tests/fixtures/mcp/v1-responses.json"
SECTIONS = ["columns", "indexes", "foreign_keys", "constraints", "models", "relationships"]


def record(path: Path) -> dict:
    query = SnapshotQuery(path)
    calls = {"overview": query.overview(limit=100), "overview:page": query.overview(limit=2, offset=1)}
    for text in ("order", "id", "tag", "ONETOMANY", "zzz"):
        calls[f"search:{text}"] = query.search(text, limit=100)
    for table in query.tables:
        for section in SECTIONS:
            calls[f"table:{table}:{section}"] = query.get_table(table, section, limit=100)
        for kind in ("orm", "foreign_key"):
            calls[f"relationships:{table}:{kind}"] = query.relationships(table, kind, limit=100)
    for model in query.models:
        calls[f"model:{model}"] = query.get_model(model, limit=100)
    for kind in ("orm", "foreign_key"):
        calls[f"relationships:*:{kind}"] = query.relationships(None, kind, limit=100)
    return calls


class VersionOneResponses(unittest.TestCase):
    def test_responses_are_unchanged(self):
        golden = json.loads(GOLDEN.read_text())
        for snapshot in SNAPSHOTS:
            with self.subTest(snapshot=snapshot):
                current = json.loads(json.dumps(record(ROOT / snapshot)))
                self.assertEqual(sorted(current), sorted(golden[snapshot]))
                for call, response in golden[snapshot].items():
                    self.assertEqual(current[call], response, call)


if __name__ == "__main__":
    if "--record" in sys.argv:
        GOLDEN.write_text(json.dumps({s: record(ROOT / s) for s in SNAPSHOTS}, indent=1, sort_keys=True) + "\n")
    else:
        unittest.main()
