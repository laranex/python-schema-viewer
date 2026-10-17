"""The schema comparisons a project asks for, computed on a document's model with the shared algorithm
(contract/comparison.py) and stored in its ``comparisons`` array.

Comparisons read only what a document already holds: no database connection, no application import. Requests are
expanded with the shared ``expand_comparison_requests`` (a target schema ``*`` stands for every schema the target
connection's inspection read except the baseline's own scope), stored with ``store_comparisons``, which adds a
``comparison_incomplete`` diagnostic to each incomplete comparison.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from . import model as m
from .config import ComparisonConfig
from .contract import to_json
from .contract.comparison import (RequestedComparison, compare_scopes, expand_comparison_requests, request_key,
                                  scope_name)

MAX_VALUE_CHARACTERS = 80


def requests(specs: Iterable[ComparisonConfig]) -> list[RequestedComparison]:
    """Comparisons as the contract requests them: by connection name, ``*`` targets unexpanded."""
    return [{side: {"connection": scope.connection, "schema": scope.schema}
             for side, scope in (("baseline", spec.baseline), ("target", spec.target))} for spec in specs]


def expand(document: m.Document, specs: Sequence[ComparisonConfig], *, strict: bool = True
           ) -> list[tuple[m.Scope, m.Scope, ComparisonConfig]]:
    """The scope pairs the comparisons stand for, each with the comparison that requested it (its limit and
    overrides). Expansion errors (an unknown connection, a ``*`` target with no schemas or none left, a comparison
    listed twice) raise ValueError, unless ``strict`` is false: then their requests are skipped."""
    pairs, errors = expand_comparison_requests(document, requests(specs))
    if errors and strict:
        raise ValueError("\n".join(e.message for e in errors))
    by_request = {request_key(r): spec for r, spec in zip(requests(specs), specs)}
    return [(p.baseline, p.target, by_request[request_key(p.request)]) for p in pairs]


def compute(document: m.Document, specs: Sequence[ComparisonConfig], *, strict: bool = True
            ) -> tuple[m.Comparison, ...]:
    """The comparisons, sorted by id; ``strict`` as for ``expand``. Store them with ``store_comparisons``."""
    comparisons = [compare_scopes(document, baseline, target, overrides=spec.overrides, limit=spec.limit)
                   for baseline, target, spec in expand(document, specs, strict=strict)]
    return tuple(sorted(comparisons, key=lambda c: c.id))


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
        lines.append(f"{scope_name(document, c.baseline)} -> {scope_name(document, c.target)} ({state}): "
                     f"{s.missing} missing, {s.extra} extra, {s.changed} changed{omitted}")
        for d in c.differences:
            side = d.baseline or d.target
            label = side.label if side else "/".join(d.key)
            fields = "; ".join(f"{f.field} {_value(f.baseline)} -> {_value(f.target)}" for f in d.fields)
            lines.append(f"  {d.severity:<7} {d.certainty:<7} {d.change:<7} {d.object_type:<10} {label}"
                         + (f"  {fields}" if fields else ""))
    return "\n".join(lines)
