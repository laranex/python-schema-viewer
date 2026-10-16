# Distributable agent skills validation

Executed on 2026-10-16. No commits, pushes, global skill installations, Laravel repository edits, or reference application changes were performed.

## Implementation

`skills/` contains five application-facing bundles: `python-schema-viewer`, `schema-discovery`, `relationship-analysis`, `schema-diagnostics`, and `schema-viewer-mcp`. Each has portable YAML frontmatter and MIT licensing. Schema discovery includes its own snapshot reference. Contributor instructions remain in root `AGENTS.md`.

The distribution follows the sibling Laravel repository's `skills/<name>/SKILL.md` layout. Command options and supported agent paths were checked against the [official Skills CLI source documentation](https://github.com/vercel-labs/skills). Skills use snapshot-only MCP tools with JSON fallback; they do not add live reflection, a hints tool, or Laravel commands.

## Executed checks

- All five bundles passed the skill-creator `quick_validate.py` validator.
- Four tests in `tests.test_distributed_skills` passed. They validate YAML metadata, isolated copies into Codex and Claude directory layouts, bundled references, broken/escaping reference rejection, JSON examples for all five query APIs, and execution of the snapshot-reader example in a temporary project.
- Full Python suite: 37 tests ran, 36 passed, one loopback HTTP test skipped because this environment denies socket binding. The real MCP subprocess stdio integration test passed.
- Documentation demo tests: two passed.
- `git diff --check` passed. Ruff could not run because its executable is unavailable in this environment.

Copy portability tests do not demonstrate installation by the Skills CLI or interactive agent behavior.

## CLI attempts and remaining checks

The remote discovery command was attempted through npx:

```bash
npx --yes skills add laranex/python-schema-viewer --list
```

Both local discovery and installation were also attempted from an isolated temporary project:

```bash
npx --yes skills add /absolute/path/to/python-schema-viewer/skills --list
npx --yes skills add /absolute/path/to/python-schema-viewer/skills \
  --skill '*' --agent codex claude-code --yes
```

All three attempts failed before CLI execution because npm could not resolve `registry.npmjs.org` (`ENOTFOUND`). Real CLI discovery, installed compatibility links, and client activation remain unverified. Retry on a network-enabled machine. Remote commands see published GitHub contents, not these uncommitted files.

The CLI also searches `.agents/skills/`. The repository no longer commits legacy copies there, so a repository-root catalog lists only the five application skills.

No skill synchronization or installation helper was introduced. The canonical distribution is independent of legacy local copies.
