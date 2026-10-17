"""The CI drift gate: a saved document's diagnostics, comparisons and migration tracks checked against thresholds,
as a laranex-schema-gate 1.0 report (contract/gate.py) and a readable summary.

Reads only the document: never a database, an ORM, application code or a subprocess, and never writes the document.
docs/contract.md, "Gate reports", defines the findings, errors and exit codes; docs/check.md describes the command.
"""

from __future__ import annotations

import re
from typing import Any

from . import model as m
from .compare import scope_label
from .contract.comparison import COMPARISON_FEATURES
from .contract.gate import RANK, gate_report, qualifies

# Migration statuses that are findings, with their default severity; unknown is an error, up_to_date nothing.
MIGRATION_SEVERITY = {"behind": "error", "diverged": "error", "uninitialized": "warning"}
STATE_TEXT = {"unavailable": "were not read", "partial": "were read only partly", "unknown": "have unknown coverage"}


def _snake(name: str) -> str:
    return re.sub(r"[A-Z]", lambda c: f"_{c.group().lower()}", name)


def _words(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _clause(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _connection(document: m.Document, connection_id: str) -> m.Connection | None:
    return next((c for c in document.connections if c.id == connection_id), None)


def track_label(document: m.Document, track: m.MigrationTrack) -> str:
    """The tool and the track's schema, or its connection's name for an unqualified track."""
    connection = _connection(document, track.connection)
    return f"{track.provider} · {track.schema if track.schema is not None else (connection.name if connection else track.connection)}"


def findings(document: m.Document) -> list[dict[str, Any]]:
    """Every diagnostic, every listed difference of every comparison, and every migration track whose status is
    behind, diverged or uninitialized."""
    found = [{"source": "diagnostic", "id": d.id, "kind": d.kind, "severity": d.severity, "certainty": d.certainty,
              "label": d.subject.label or d.subject.id or d.subject.type, "message": d.message}
             for d in document.diagnostics]
    for c in document.comparisons:
        for d in c.differences:
            side = d.baseline or d.target
            message = (f"{', '.join(f.field for f in d.fields)} differ" if d.change == "changed"
                       else "Only in the baseline" if d.change == "missing" else "Only in the target")
            found.append({"source": "comparison", "id": d.id, "kind": f"{_snake(d.object_type)}_{d.change}",
                          "severity": d.severity, "certainty": d.certainty,
                          "label": side.label if side else ".".join(d.key), "message": message})
    for t in document.migrations:
        if t.status not in MIGRATION_SEVERITY:
            continue
        if t.status == "behind":
            message = f"{len(t.pending or ())} revisions are not applied"
        elif t.status == "diverged":
            message = f"The version table records overlapping revisions: {', '.join(t.current or ())}"
        elif t.version_table is None:
            message = "The database has no version table"
        else:
            message = "The version table records no revision"
        found.append({"source": "migration", "id": t.id, "kind": f"migration_{t.status}",
                      "severity": MIGRATION_SEVERITY[t.status], "certainty": "certain",
                      "label": track_label(document, t), "message": message})
    return found


def _incomplete(document: m.Document, comparison: m.Comparison) -> str:
    """Why a comparison is incomplete: omitted differences, then each side's coverage gaps."""
    parts = [f"{comparison.omitted} differences omitted"] if comparison.omitted else []
    gaps: dict[str, list[str]] = {}
    schemas: list[str] = []
    for scope in (comparison.baseline, comparison.target):
        connection = _connection(document, scope.connection)
        coverage = None if connection is None else (
            connection.coverage.declared if document.source == "declared" else connection.coverage.database)
        if connection is None or coverage is None:
            gaps.setdefault("unknown", []).append(f"the {scope_label(document, scope)} scope")
            continue
        for feature in COMPARISON_FEATURES:
            state = coverage.features.get(feature, "unknown")
            if state not in ("complete", "not_applicable"):
                gaps.setdefault(state if state in STATE_TEXT else "unknown", []).append(
                    re.sub(r"[A-Z]", lambda c: f" {c.group().lower()}", feature))
        schema = scope.schema if scope.schema is not None else connection.default_schema
        if schema is not None and coverage.schemas is not None and schema not in coverage.schemas:
            schemas.append(f"schema {schema} was not read")
    for state, text in STATE_TEXT.items():
        names = list(dict.fromkeys(gaps.get(state, [])))
        if names:
            parts.append(f"{_words(names)} {text}")
    parts += list(dict.fromkeys(schemas))
    return f"Comparison {comparison.id} is incomplete" + (f": {_clause(parts)}." if parts else ".")


def _unknown(document: m.Document, track: m.MigrationTrack) -> str:
    if track.current is None:
        reason = "the version table could not be read"
    elif track.history is None:
        reason = "the migration scripts could not be read"
    elif track.unknown_revisions:
        reason = f"the database records revisions the scripts do not have: {', '.join(track.unknown_revisions)}"
    else:
        reason = "the history is missing revisions it needs"
    return f"Migration track {track.id} ({track_label(document, track)}) has an unknown status: {reason}."


def errors(document: m.Document, version: str) -> list[dict[str, str]]:
    """What keeps the check from completing: incomplete comparisons, unknown migration statuses, inspection errors
    recorded as diagnostics, and a newer 2.x document that could be validated only leniently."""
    found = [{"kind": "comparison_incomplete", "message": _incomplete(document, c)}
             for c in document.comparisons if not c.complete]
    found += [{"kind": "migration_unknown", "message": _unknown(document, t)}
              for t in document.migrations if t.status == "unknown"]
    found += [{"kind": "inspection_failed",
               "message": f"Diagnostic {d.id} ({d.kind}, {d.subject.label or d.subject.id or d.subject.type}): "
                          f"{d.message}"}
              for d in document.diagnostics if d.category == "inspection" and d.severity == "error"]
    if version != "2.0":
        found.append({"kind": "validation_incomplete",
                      "message": f"The document is laranex-schema {version}; this version validates 2.0 strictly, so "
                                 "what a newer minor version added was not checked. Upgrade laranex-schema-viewer."})
    return found


def check(document: m.Document, version: str, thresholds: dict[str, str]) -> dict[str, Any]:
    """The gate report of a document."""
    return gate_report(thresholds, findings(document), errors(document, version))


def invalid(thresholds: dict[str, str], message: str) -> dict[str, Any]:
    """The report for a document that could not be read or validated."""
    return gate_report(thresholds, [], [{"kind": "invalid_document", "message": message}])


def summary(report: dict[str, Any], document: m.Document | None = None) -> str:
    """A readable, deterministic summary: the verdict, what was checked, the errors and the qualifying findings."""
    t, counts = report["thresholds"], report["counts"]
    verdict = {0: "PASSED", 1: "FAILED", 2: "INCOMPLETE"}[report["exitCode"]]
    lines = [f"Schema check {verdict} (exit code {report['exitCode']}): {counts['qualifying']} of {counts['findings']} "
             f"findings meet the thresholds (severity {t['severity']} or above, "
             f"{'certain only' if t['certainty'] == 'certain' else 'certain or likely'})"]
    if document is not None:
        incomplete = sum(1 for c in document.comparisons if not c.complete)
        lines.append(f"Checked {len(document.comparisons)} comparisons"
                     + (f" ({incomplete} incomplete)" if incomplete else "")
                     + f", {len(document.migrations)} migration tracks and {len(document.diagnostics)} diagnostics")
    if report["errors"]:
        lines.append("Errors (the check is incomplete and never passes):")
        lines += [f"  {e['kind']}: {e['message']}" for e in report["errors"]]
    qualifying = sorted((f for f in report["findings"] if qualifies(f, t)),
                        key=lambda f: (-RANK[f["severity"]], f["source"], f["label"], f["id"]))
    if qualifying:
        lines.append("Findings that meet the thresholds, most severe first:")
        lines += [f"  {f['severity']:<7} {f['certainty']:<7} {f['source']:<10} {f['kind']}  {f['label']}: "
                  f"{f['message']} [{f['id']}]" for f in qualifying]
    others = counts["findings"] - counts["qualifying"]
    if others:
        lines.append(f"{others} findings below the thresholds are listed in the JSON report (--json or --output)")
    return "\n".join(lines)
