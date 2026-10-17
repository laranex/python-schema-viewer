"""The schema comparisons a project asks for, computed on a document's model with the shared algorithm
(contract/comparison.py) and stored in its ``comparisons`` array.

Comparisons read only what a document already holds: no database connection, no application import. A target schema
``*`` expands to every schema the target connection's inspection read (its coverage ``schemas``) except the baseline's
own scope. An incomplete comparison gets a ``comparison_incomplete`` diagnostic that says why.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from . import model as m
from .config import WILDCARD, ComparisonConfig, ScopeConfig
from .contract import finalize, to_json
from .contract.comparison import SeverityOverride, compare_scopes, coverage_gaps

MAX_VALUE_CHARACTERS = 80


def scope_label(document: m.Document, scope: m.Scope) -> str:
    """A scope as the command line spells it: the connection's name, then ``:schema``."""
    name = next((c.name for c in document.connections if c.id == scope.connection), scope.connection)
    return name if scope.schema is None else f"{name}:{scope.schema}"


def _connection(document: m.Document, scope: ScopeConfig) -> m.Connection:
    connection = next((c for c in document.connections if c.name == scope.connection), None)
    if connection is None:
        names = ", ".join(c.name for c in document.connections) or "none"
        raise ValueError(f"A comparison names connection {scope.connection}, which the document does not have "
                         f"(connections: {names})")
    return connection


def scopes(document: m.Document, spec: ComparisonConfig) -> list[tuple[m.Scope, m.Scope]]:
    """The scope pairs one configured comparison stands for: one, or one per inspected schema for a ``*`` target."""
    baseline = m.Scope(connection=_connection(document, spec.baseline).id, schema=spec.baseline.schema)
    target_connection = _connection(document, spec.target)
    if spec.target.schema != WILDCARD:
        return [(baseline, m.Scope(connection=target_connection.id, schema=spec.target.schema))]
    coverage = (target_connection.coverage.declared if document.source == "declared"
                else target_connection.coverage.database)
    if coverage is None or coverage.schemas is None:
        raise ValueError(f"Comparison target {spec.target} needs a connection whose inspection read named schemas; "
                         f"{target_connection.name} has none")

    def same_scope(schema: str) -> bool:
        return target_connection.id == baseline.connection and (
            schema == baseline.schema or (baseline.schema is None and schema == target_connection.default_schema))

    return [(baseline, m.Scope(connection=target_connection.id, schema=schema))
            for schema in sorted(set(coverage.schemas)) if not same_scope(schema)]


def compute(document: m.Document, specs: Iterable[ComparisonConfig]
            ) -> tuple[tuple[m.Comparison, ...], tuple[m.Diagnostic, ...]]:
    """The comparisons, sorted by id, and a diagnostic for each incomplete one. A pair named explicitly takes
    precedence over the same pair from a ``*`` expansion; naming one pair twice otherwise is an error."""
    chosen: dict[tuple[m.Scope, m.Scope], tuple[ComparisonConfig, bool]] = {}
    for spec in specs:
        explicit = spec.target.schema != WILDCARD
        for pair in scopes(document, spec):
            if pair in chosen:
                if chosen[pair][1] == explicit:
                    raise ValueError(f"Comparison {scope_label(document, pair[0])}={scope_label(document, pair[1])} "
                                     "is configured more than once")
                if not explicit:
                    continue
            chosen[pair] = (spec, explicit)
    comparisons, diagnostics = [], []
    for (baseline, target), (spec, _) in chosen.items():
        overrides = [SeverityOverride(object_type=o.object_type, change=o.change, severity=o.severity, field=o.field)
                     for o in spec.overrides]
        comparison = compare_scopes(document, baseline, target, overrides=overrides, limit=spec.limit)
        comparisons.append(comparison)
        if not comparison.complete:
            diagnostics.append(_incomplete(document, comparison, spec.limit))
    return tuple(sorted(comparisons, key=lambda c: c.id)), tuple(diagnostics)


def _incomplete(document: m.Document, comparison: m.Comparison, limit: int) -> m.Diagnostic:
    reasons = []
    if comparison.omitted:
        total = comparison.summary.missing + comparison.summary.extra + comparison.summary.changed
        reasons.append(f"{comparison.omitted} of {total} differences are left out by the limit of {limit}")
    for side, scope in (("baseline", comparison.baseline), ("target", comparison.target)):
        gaps = coverage_gaps(document, scope)
        if gaps:
            reasons.append(f"the {side} {scope_label(document, scope)} is not completely covered ({', '.join(gaps)})")
    label = f"{scope_label(document, comparison.baseline)}={scope_label(document, comparison.target)}"
    return m.Diagnostic(kind="comparison_incomplete", category="inspection", severity="warning", certainty="certain",
                        subject=m.Subject(type="comparison", id=comparison.id, label=label),
                        message=f"Comparison {label} is incomplete: {'; '.join(reasons)}")


def store(document: m.Document, comparisons: Sequence[m.Comparison], diagnostics: Sequence[m.Diagnostic]
          ) -> m.Document:
    """The document with computed comparisons and their diagnostics. Comparisons it already holds for the same
    scopes, and their diagnostics, are replaced; others are kept."""
    ids = {c.id for c in comparisons}
    kept = [c for c in document.comparisons if c.id not in ids]
    previous = [d for d in document.diagnostics if not (d.subject.type == "comparison" and d.subject.id in ids)]
    return finalize(m.replace(document, comparisons=tuple(sorted([*kept, *comparisons], key=lambda c: c.id)),
                              diagnostics=(*previous, *diagnostics)))


def _value(value: object) -> str:
    text = json.dumps(to_json(value), ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= MAX_VALUE_CHARACTERS else text[:MAX_VALUE_CHARACTERS - 1] + "…"


def describe(document: m.Document, comparisons: Iterable[m.Comparison]) -> str:
    """A readable summary: one line per comparison, then one per listed difference."""
    lines = []
    for c in comparisons:
        s = c.summary
        state = "complete" if c.complete else "incomplete"
        omitted = f", {c.omitted} not listed" if c.omitted else ""
        lines.append(f"{scope_label(document, c.baseline)} -> {scope_label(document, c.target)} ({state}): "
                     f"{s.missing} missing, {s.extra} extra, {s.changed} changed{omitted}")
        for d in c.differences:
            side = d.baseline or d.target
            label = side.label if side else "/".join(d.key)
            fields = "; ".join(f"{f.field} {_value(f.baseline)} -> {_value(f.target)}" for f in d.fields)
            lines.append(f"  {d.severity:<7} {d.certainty:<7} {d.change:<7} {d.object_type:<10} {label}"
                         + (f"  {fields}" if fields else ""))
    return "\n".join(lines)
