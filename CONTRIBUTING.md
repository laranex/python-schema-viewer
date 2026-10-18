# Contributing

[AGENTS.md](AGENTS.md) describes the architecture, development commands, CI matrix and documentation maintenance. This page lists what every change must respect.

## Set up and validate

```bash
python -m venv .venv && source .venv/bin/activate
python scripts/fetch_ui.py    # the shared UI assets ui.lock.json pins; rerun after every ui.lock.json change
pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check .
python -m build && python scripts/check_wheel.py
laranex-schema-viewer scan --tracked --untracked
```

Documentation changes: `cd .gh-page && npm ci && npm run check`. Skill changes: `python -m unittest tests.test_distributed_skills -v`.

## Schema documents and fixtures

- Every fixture, demo and test schema must be synthetic. Never use a real application's or database's schema, and never point the database tests at a real database.
- Never commit real schema snapshots or their exports (offline HTML, JSON, DBML, Mermaid, Markdown, SVG, PNG), gate or scan reports, credentials, or `.env` files.
- A new or changed synthetic document that the scanner detects needs a regenerated allowlist entry and review: run `PYTHONPATH=src python scripts/write_allowlist.py` after changing `.gh-page/demo/` or running `python scripts/record_mcp_conformance.py`, and commit `.laranex-schema-allowlist.json` with the change. `tests/test_scan.py` fails while it is not current. Only the directories listed in the script are allowlisted; an entry is a review decision, not proof that the content is harmless.
- Run `laranex-schema-viewer scan --tracked --untracked` before committing; it must exit 0. The `laranex-schema-scan` hook in `.pre-commit-hooks.yaml` runs the same scanner on the staged files.
- `tests/fixtures/contract` and the viewer under `src/laranex_schema_viewer/static/viewer/` come from the pinned UI tarball and are git-ignored; never commit or edit them.

## Security issues

Do not open a public issue for a vulnerability; report it as [SECURITY.md](SECURITY.md) describes.
