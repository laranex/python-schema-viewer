"""The CI drift gate and its laranex-schema-gate 1.x report, a port of laranex-schema-viewer-ui's gate.ts.

``gate_document()`` checks a document's comparisons, drift and reconciliation diagnostics and migration tracks against
thresholds and returns the report, with the reference's findings, errors, labels, messages and order;
``validate_gate_report()`` checks a report. docs/contract.md, "Gate reports", is the normative description;
contract/conformance/gate/ checks it. The shape both checks a report and describes itself as JSON Schema, so
``gate_json_schema()`` reproduces the shared ``gate-report.schema.json``. Reads only the document it is given.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .. import model as m
from ..model import FEATURES
from .comparison import scope_covered, table_label
from .ids import by_list
from .serialize import FORMAT, MAJOR, VERSION
from .shape import (CHANGES, OBJECT_TYPES, Context, Shape, array, boolean, integer, literal, nullable, obj, one_of,
                    string, stringify)
from .validate import validate_document

GATE_FORMAT = "laranex-schema-gate"
GATE_VERSION = "1.0"
GATE_SCHEMA_ID = "https://laranex.github.io/schema-viewer-ui/laranex-schema-gate/1.0.json"
SEVERITIES = ("error", "warning", "info")
CERTAINTIES = ("certain", "likely")
SOURCES = ("diagnostic", "comparison", "migration")
EXIT_CODES = (0, 1, 2)
GATE_ERROR_KINDS = ("comparison_incomplete", "comparison_not_found", "coverage_incomplete", "inspection_failed",
                    "invalid_document", "migration_unknown", "validation_incomplete")
DEFAULT_THRESHOLDS = {"severity": "warning", "certainty": "certain"}
WILDCARD = "*"

RANK = {"info": 0, "warning": 1, "error": 2}
PREFIX = {"diagnostic": "n", "comparison": "x", "migration": "m"}
NOUN = {"diagnostic": "diagnostic", "comparison": "difference", "migration": "migration track"}
# The migration statuses that are findings, with their severity; up_to_date is none, unknown an error.
MIGRATION_SEVERITY = {"behind": "error", "diverged": "error", "uninitialized": "warning"}
REQUIRED_FEATURES = ("tables", "columns")


class _Unparsed:
    def __repr__(self) -> str:
        return "UNPARSED"


# The input of a gate whose text could not be parsed as JSON (the reference's ``undefined``); None is JSON null.
UNPARSED: Any = _Unparsed()


def _snake(name: str) -> str:
    return re.sub(r"[A-Z]", lambda c: f"_{c.group().lower()}", name)


COMPARISON_KINDS = tuple(f"{_snake(o)}_{c}" for o in OBJECT_TYPES for c in CHANGES)


def _exit_code() -> Shape:
    def check(value: Any, path: str, context: Context) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in EXIT_CODES:
            context.errors.append(f"{path}: expected 0, 1 or 2, got {stringify(value)}")

    return Shape({"enum": list(EXIT_CODES)}, check)


def _count() -> Shape:
    return integer(minimum=0)


_severity, _certainty = one_of(SEVERITIES), one_of(CERTAINTIES)
_requested_scope = obj({"connection": string(min_length=1), "schema": nullable(string())})
gate_report_shape = obj({
    "format": literal(GATE_FORMAT),
    "version": string(pattern="^1\\.(0|[1-9][0-9]*)$"),
    "passed": boolean(),
    "exitCode": _exit_code(),
    "thresholds": obj({"severity": _severity, "certainty": _certainty}),
    "requested": array(obj({"baseline": _requested_scope, "target": _requested_scope})),
    "comparisons": array(string(min_length=1)),
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
    "errors": array(obj({"kind": one_of(GATE_ERROR_KINDS), "message": string(min_length=1)})),
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


def _scope_key(scope: Mapping[str, Any]) -> tuple[str, tuple[int, str]]:
    # null sorts before any string.
    return scope["connection"], (0, "") if scope["schema"] is None else (1, scope["schema"])


def _request_key(request: Mapping[str, Any]) -> tuple[Any, ...]:
    return _scope_key(request["baseline"]), _scope_key(request["target"])


def _spell(connection: str, schema: str | None) -> str:
    """A scope as people write it: the connection's name, then ``:schema``."""
    return connection if schema is None else f"{connection}:{schema}"


def _report(thresholds: dict[str, str], requested: list[dict[str, Any]], comparisons: list[str],
            findings: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    """The report for what was found. Findings are sorted by source, id and kind; errors by kind and message, each
    listed once."""
    found = sorted(findings, key=lambda f: (f["source"], f["id"], f["kind"]))
    problems = [{"kind": kind, "message": message} for kind, message in sorted({(e["kind"], e["message"])
                                                                               for e in errors})]
    qualifying = sum(1 for f in found if qualifies(f, thresholds))
    exit_code = 2 if problems else 1 if qualifying else 0
    return {
        "format": GATE_FORMAT,
        "version": GATE_VERSION,
        "passed": exit_code == 0,
        "exitCode": exit_code,
        "thresholds": thresholds,
        "requested": requested,
        "comparisons": comparisons,
        "counts": {"findings": len(found), "qualifying": qualifying,
                   **{s: sum(1 for f in found if f["severity"] == s) for s in SEVERITIES}},
        "findings": found,
        "errors": problems,
    }


def _invalid(data: Any) -> tuple[m.Document | None, str | None]:
    """The document as the gate reads it, validated strictly (2.0) or leniently (a newer 2.x), else the
    invalid_document message."""
    if data is UNPARSED:
        return None, "The input could not be parsed as JSON."
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("version"), str):
        return None, "The input is not a laranex-schema document."
    version = data["version"]
    if version.split(".")[0] != str(MAJOR):
        return None, f"The document is laranex-schema {version}; the gate reads {MAJOR}.x."
    document, _ = validate_document(data, strict=version == VERSION)
    return (document, None) if document is not None else (None, f"The document fails laranex-schema {version} validation.")


def gate_document(data: Any, *, thresholds: Mapping[str, str | None] | None = None,
                  requested: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Gates a document: ``data`` is the parsed JSON (``UNPARSED`` when the text could not be parsed). Never raises
    for any input; an input that is not a valid laranex-schema 2.x document gives an invalid_document report.

    ``thresholds`` default separately to severity ``warning`` and certainty ``certain``. ``requested`` lists
    ``{baseline: {connection, schema}, target: {connection, schema}}`` by connection name, a target schema ``*``
    standing for every schema the target connection's inspection read; empty or None checks every stored comparison.
    """
    limits = {**DEFAULT_THRESHOLDS, **{k: v for k, v in (thresholds or {}).items() if v is not None}}
    wanted_requests: list[dict[str, Any]] = []
    for r in sorted(requested or (), key=_request_key):
        if not wanted_requests or _request_key(wanted_requests[-1]) != _request_key(r):
            wanted_requests.append({side: {"connection": r[side]["connection"], "schema": r[side]["schema"]}
                                    for side in ("baseline", "target")})
    doc, problem = _invalid(data)
    if doc is None:
        return _report(limits, wanted_requests, [], [], [{"kind": "invalid_document", "message": problem}])
    version = data["version"]
    errors: list[dict[str, str]] = []
    if version != VERSION:
        errors.append({"kind": "validation_incomplete",
                       "message": f"The document is laranex-schema {version}; only {VERSION} is validated strictly, "
                                  f"so what {version} adds was not checked."})

    connections = {c.id: c for c in doc.connections}
    by_name = {c.name: c for c in doc.connections}

    def name_of(connection_id: str) -> str:
        c = connections.get(connection_id)
        return c.name if c is not None else connection_id

    def pair(baseline: m.Scope, target: m.Scope) -> str:
        return f"{_spell(name_of(baseline.connection), baseline.schema)} → {_spell(name_of(target.connection), target.schema)}"

    def side_coverage(c: m.Connection) -> m.Coverage | None:
        return c.coverage.declared if doc.source == "declared" else c.coverage.database

    # The comparisons to check: every stored one, or only the requested ones.
    checked = list(doc.comparisons)
    if wanted_requests:
        wanted: set[str] = set()
        for r in wanted_requests:
            b, t = r["baseline"], r["target"]
            request = f"{_spell(b['connection'], b['schema'])} → {_spell(t['connection'], t['schema'])}"
            baseline, target = by_name.get(b["connection"]), by_name.get(t["connection"])
            for name in dict.fromkeys((b["connection"], t["connection"])):
                if name not in by_name:
                    errors.append({"kind": "comparison_not_found",
                                   "message": f"Requested comparison {request} names connection {name}, which the "
                                              "document does not have."})
            if baseline is None or target is None:
                continue
            schemas: list[str | None] = [t["schema"]]
            if t["schema"] == WILDCARD:
                coverage = side_coverage(target)
                read = coverage.schemas if coverage is not None else None
                if read is None:
                    errors.append({"kind": "comparison_not_found",
                                   "message": f"Requested comparison {request} cannot be expanded: the inspection of "
                                              f"connection {target.name} lists no schemas."})
                    continue

                def own(s: str) -> bool:
                    return target.id == baseline.id and (
                        s == b["schema"] or (b["schema"] is None and s == target.default_schema))

                schemas = [s for s in sorted(set(read)) if not own(s)]
                if not schemas:
                    errors.append({"kind": "comparison_not_found",
                                   "message": f"Requested comparison {request} matches no schema."})
            for schema in schemas:
                found = next((c for c in doc.comparisons
                              if c.baseline.connection == baseline.id and c.baseline.schema == b["schema"]
                              and c.target.connection == target.id and c.target.schema == schema), None)
                if found is not None:
                    wanted.add(found.id)
                else:
                    errors.append({"kind": "comparison_not_found",
                                   "message": f"Requested comparison {_spell(baseline.name, b['schema'])} → "
                                              f"{_spell(target.name, schema)} is not in the document."})
        checked = [c for c in doc.comparisons if c.id in wanted]

    findings: list[dict[str, Any]] = []
    for c in checked:
        label = pair(c.baseline, c.target)
        for d in c.differences:
            ref = d.baseline or d.target
            findings.append({
                "source": "comparison", "id": d.id, "kind": f"{_snake(d.object_type)}_{d.change}",
                "severity": d.severity, "certainty": d.certainty,
                "label": f"{label} · {ref.label if ref is not None else '.'.join(d.key)}",
                "message": ("Only in the baseline" if d.change == "missing" else "Only in the target"
                            if d.change == "extra" else f"Differs in {', '.join(f.field for f in d.fields)}"),
            })
        if not c.complete:
            reasons: list[str] = []
            if c.omitted > 0:
                total = c.summary.missing + c.summary.extra + c.summary.changed
                reasons.append(f"{c.omitted} of {total} differences omitted")
            for side, scope in (("baseline", c.baseline), ("target", c.target)):
                connection = connections[scope.connection]
                if not scope_covered(side_coverage(connection), connection, scope.schema):
                    reasons.append(f"the {side} {_spell(connection.name, scope.schema)} was not inspected completely")
            errors.append({"kind": "comparison_incomplete",
                           "message": f"Comparison {label} is incomplete"
                                      + (f": {'; '.join(reasons)}." if reasons else ".")})

    # Required database coverage in database and combined documents: tables and columns complete, and no feature
    # unavailable. A partial feature (a dialect's known limit) is judged by the comparisons that read it.
    if doc.source != "declared":
        for c in doc.connections:
            features = c.coverage.database.features if c.coverage.database is not None else {}
            gaps = [f"{f} {features.get(f, 'unknown')}" for f in FEATURES
                    if features.get(f) == "unavailable" or (f in REQUIRED_FEATURES and features.get(f) != "complete")]
            if gaps:
                errors.append({"kind": "coverage_incomplete",
                               "message": f"Connection {c.name}: the database inspection is incomplete: "
                                          f"{', '.join(gaps)}."})

    label_of = _diagnostic_label(doc, pair, name_of)
    for d in doc.diagnostics:
        if d.category in ("drift", "reconciliation"):
            findings.append({"source": "diagnostic", "id": d.id, "kind": d.kind, "severity": d.severity,
                             "certainty": d.certainty, "label": label_of(d), "message": d.message})
        elif d.category == "inspection" and d.severity == "error":
            errors.append({"kind": "inspection_failed", "message": f"{d.kind} on {label_of(d)}: {d.message}"})

    for t in doc.migrations:
        where = _track_label(t, name_of)
        severity = MIGRATION_SEVERITY.get(t.status)
        if severity is not None:
            if t.status == "behind":
                message = f"{t.provider} revisions not applied: {', '.join(t.pending or ())}"
            elif t.status == "diverged":
                message = (f"The {t.provider} version table records overlapping revisions, one an ancestor of "
                           f"another: {', '.join(t.current or ())}")
            elif t.version_table is None:
                message = f"The database has no {t.provider} version table"
            else:
                message = f"The {t.provider} version table records no revision"
            findings.append({"source": "migration", "id": t.id, "kind": f"migration_{t.status}", "severity": severity,
                             "certainty": "certain", "label": where, "message": message})
        elif t.status == "unknown":
            if t.current is None:
                reason = "the version table could not be read"
            elif t.history is None:
                reason = "the migration scripts could not be read"
            elif t.unknown_revisions:
                reason = f"the database records revisions the scripts do not have: {', '.join(t.unknown_revisions)}"
            else:
                reason = "the history is missing revisions it needs"
            errors.append({"kind": "migration_unknown",
                           "message": f"Migration track {where} ({t.provider}) has an unknown status: {reason}."})

    return _report(limits, wanted_requests, sorted(c.id for c in checked), findings, errors)


def _track_label(t: m.MigrationTrack, name_of: Callable[[str], str]) -> str:
    return name_of(t.connection) if t.schema is None else f"{name_of(t.connection)} · {t.schema}"


def _diagnostic_label(doc: m.Document, pair: Callable[[m.Scope, m.Scope], str],
                      name_of: Callable[[str], str]) -> Callable[[m.Diagnostic], str]:
    """A diagnostic's label: the subject's own label, else the name its id resolves to, else the id ("document" for
    the document)."""
    tables: dict[str, tuple[m.Table, m.Connection]] = {}
    for connection in doc.connections:
        for table in connection.tables:
            tables[table.id] = (table, connection)

    def where(table: m.Table, connection: m.Connection) -> str:
        return f"{connection.name} · {table_label(table)}"

    def resolve(d: m.Diagnostic) -> str | None:
        kind, id_, columns = d.subject.type, d.subject.id, d.subject.columns
        if id_ is None:
            return None
        if kind == "connection":
            return next((c.name for c in doc.connections if c.id == id_), None)
        if kind in ("table", "column"):
            found = tables.get(id_)
            if found is None:
                return None
            return where(*found) if kind == "table" else f"{where(*found)}.{', '.join(columns or ())}"
        if kind in ("index", "foreignKey", "constraint"):
            for table, connection in tables.values():
                entities = (table.indexes if kind == "index" else table.foreign_keys if kind == "foreignKey"
                            else table.constraints)
                entity = next((e for e in entities if e.id == id_), None)
                if entity is not None:
                    return f"{where(table, connection)}.{entity.name if entity.name is not None else entity.id}"
            return None
        if kind == "model":
            return next((x.class_name for x in doc.models if x.id == id_), None)
        if kind == "relationship":
            r = next((x for x in doc.relationships if x.id == id_), None)
            if r is None:
                return None
            model = next((x.class_name for x in doc.models if x.id == r.model), r.model)
            return f"{model}.{r.name}"
        if kind == "comparison":
            c = next((x for x in doc.comparisons if x.id == id_), None)
            return pair(c.baseline, c.target) if c is not None else None
        if kind == "migration":
            t = next((x for x in doc.migrations if x.id == id_), None)
            return _track_label(t, name_of) if t is not None else None
        return None

    def label(d: m.Diagnostic) -> str:
        if d.subject.label is not None:
            return d.subject.label
        resolved = resolve(d)
        if resolved is not None:
            return resolved
        return d.subject.id if d.subject.id is not None else "document"

    return label


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
            if f["source"] == "comparison" and f["kind"] not in COMPARISON_KINDS:
                errors.append(f"A comparison finding's kind is <object_type>_<change>: findings[{i}]")
            if f["source"] == "migration" and (
                    MIGRATION_SEVERITY.get(re.sub("^migration_", "", f["kind"])) != f["severity"]
                    or not f["kind"].startswith("migration_") or f["certainty"] != "certain"):
                errors.append("A migration finding is migration_behind or migration_diverged (error) or "
                              f"migration_uninitialized (warning), and certain: findings[{i}]")
            if i > 0 and by_list([findings[i - 1]["source"], findings[i - 1]["id"], findings[i - 1]["kind"]],
                                 [f["source"], f["id"], f["kind"]]) >= 0:
                errors.append(f"Findings are unique and sorted by source, id and kind: findings[{i}]")
        problems = r["errors"]
        for i, e in enumerate(problems):
            if i > 0 and by_list([problems[i - 1]["kind"], problems[i - 1]["message"]], [e["kind"], e["message"]]) >= 0:
                errors.append(f"Errors are unique and sorted by kind and message: errors[{i}]")
        if any(e["kind"] == "invalid_document" for e in problems) and (
                len(problems) > 1 or findings or r["comparisons"]):
            errors.append("A report on an invalid document has no other error, no findings and no checked comparisons")
        for i, q in enumerate(r["requested"]):
            if i > 0 and _request_key(r["requested"][i - 1]) >= _request_key(q):
                errors.append(f"Requested comparisons are unique and sorted: requested[{i}]")
        for i, c in enumerate(r["comparisons"]):
            if i > 0 and r["comparisons"][i - 1] >= c:
                errors.append(f"Checked comparisons are unique and sorted: comparisons[{i}]")
    return (None, errors) if errors else (value, [])
