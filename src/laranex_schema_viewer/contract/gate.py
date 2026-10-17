"""The laranex-schema-gate 1.x report, a port of laranex-schema-viewer-ui's gate.ts with its messages.

A separate format from the document: what the ``check`` command prints as JSON after checking a document's
diagnostics, comparisons and migration tracks against thresholds (docs/contract.md, "Gate reports"). The shape both
checks a report and describes itself as JSON Schema, so ``gate_json_schema()`` reproduces the shared
``gate-report.schema.json``. Reports are plain JSON values; nothing here knows the document.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .ids import by_list
from .shape import Context, Shape, array, boolean, integer, literal, obj, one_of, string, stringify

GATE_FORMAT = "laranex-schema-gate"
GATE_VERSION = "1.0"
GATE_SCHEMA_ID = "https://laranex.github.io/schema-viewer-ui/laranex-schema-gate/1.0.json"
SEVERITIES = ("error", "warning", "info")
CERTAINTIES = ("certain", "likely")
SOURCES = ("diagnostic", "comparison", "migration")
EXIT_CODES = (0, 1, 2)

RANK = {"info": 0, "warning": 1, "error": 2}
PREFIX = {"diagnostic": "n", "comparison": "x", "migration": "m"}
NOUN = {"diagnostic": "diagnostic", "comparison": "difference", "migration": "migration track"}


def _exit_code() -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in EXIT_CODES:
            context.errors.append(f"{path}: expected 0, 1 or 2, got {stringify(value)}")

    return Shape({"enum": list(EXIT_CODES)}, check)


def _count() -> Shape:
    return integer(minimum=0)


_severity, _certainty = one_of(SEVERITIES), one_of(CERTAINTIES)
gate_report_shape = obj({
    "format": literal(GATE_FORMAT),
    "version": string(pattern="^1\\.(0|[1-9][0-9]*)$"),
    "passed": boolean(),
    "exitCode": _exit_code(),
    "thresholds": obj({"severity": _severity, "certainty": _certainty}),
    "counts": obj({"findings": _count(), "qualifying": _count(), "error": _count(), "warning": _count(),
                   "info": _count()}),
    "findings": array(obj({
        "source": one_of(SOURCES),
        "id": string(min_length=1),
        "kind": string(pattern="^[a-z][a-z0-9_]*$"),
        "severity": _severity,
        "certainty": _certainty,
        "label": string(),
        "message": string(),
    })),
    "errors": array(obj({"kind": string(pattern="^[a-z][a-z0-9_]*$"), "message": string(min_length=1)})),
})


def gate_json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of laranex-schema-gate 1.x, as the shared gate-report.schema.json publishes it."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": GATE_SCHEMA_ID,
        "title": "laranex-schema-gate 1.x report",
        **gate_report_shape.schema,
    }


def qualifies(finding: Mapping[str, Any], thresholds: Mapping[str, str]) -> bool:
    """At least the severity threshold and, with certainty ``certain``, certain (``likely`` admits both)."""
    return (RANK[finding["severity"]] >= RANK[thresholds["severity"]]
            and (thresholds["certainty"] == "likely" or finding["certainty"] == "certain"))


def gate_report(thresholds: Mapping[str, str], findings: Iterable[Mapping[str, Any]],
                errors: Iterable[Mapping[str, str]] = ()) -> dict[str, Any]:
    """A report from its findings and errors: sorted (errors also de-duplicated), counted, and with the exit code the
    rules give: 2 for any error whatever the findings, else 1 when a finding qualifies, else 0."""
    thresholds = {"severity": thresholds["severity"], "certainty": thresholds["certainty"]}
    found = sorted((dict(f) for f in findings), key=lambda f: (f["source"], f["id"], f["kind"]))
    unique = {(e["kind"], e["message"]) for e in errors}
    problems = [{"kind": kind, "message": message} for kind, message in sorted(unique)]
    qualifying = sum(1 for f in found if qualifies(f, thresholds))
    exit_code = 2 if problems else 1 if qualifying else 0
    return {
        "format": GATE_FORMAT,
        "version": GATE_VERSION,
        "passed": exit_code == 0,
        "exitCode": exit_code,
        "thresholds": thresholds,
        "counts": {"findings": len(found), "qualifying": qualifying,
                   **{s: sum(1 for f in found if f["severity"] == s) for s in SEVERITIES}},
        "findings": found,
        "errors": problems,
    }


def validate_gate_report(value: Any, *, strict: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    """The report and no errors, or None and the errors: the shape, then the rules (validateGateReport)."""
    errors: list[str] = []
    gate_report_shape.check(value, "$", Context(errors=errors, strict=strict))
    if not errors:
        r = value
        if strict and r["version"] != GATE_VERSION:
            errors.append(f"$.version: strict validation is for version {GATE_VERSION}, got {stringify(r['version'])}")
        qualifying = sum(1 for f in r["findings"] if qualifies(f, r["thresholds"]))
        expected = 2 if r["errors"] else 1 if qualifying else 0
        if r["exitCode"] != expected:
            errors.append(f"Exit code {int(r['exitCode'])} does not follow the errors and qualifying findings "
                          f"(expected {expected})")
        if r["passed"] != (r["exitCode"] == 0):
            errors.append("passed is true exactly when the exit code is 0")
        counts = r["counts"]
        if (counts["findings"] != len(r["findings"]) or counts["qualifying"] != qualifying
                or any(counts[s] != sum(1 for f in r["findings"] if f["severity"] == s) for s in SEVERITIES)):
            errors.append("Counts do not count the findings")
        findings = r["findings"]
        for i, f in enumerate(findings):
            if not f["id"].startswith(PREFIX[f["source"]]):
                errors.append(f"A {f['source']} finding names a {NOUN[f['source']]} id: findings[{i}]")
            if i > 0 and by_list([findings[i - 1]["source"], findings[i - 1]["id"], findings[i - 1]["kind"]],
                                 [f["source"], f["id"], f["kind"]]) >= 0:
                errors.append(f"Findings are unique and sorted by source, id and kind: findings[{i}]")
        problems = r["errors"]
        for i, e in enumerate(problems):
            if i > 0 and by_list([problems[i - 1]["kind"], problems[i - 1]["message"]], [e["kind"], e["message"]]) >= 0:
                errors.append(f"Errors are unique and sorted by kind and message: errors[{i}]")
    return (None, errors) if errors else (value, [])
