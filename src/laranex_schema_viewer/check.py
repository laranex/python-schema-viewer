"""The CI drift gate (``check`` command): a thin layer over ``contract.gate.gate_document``, the port of the shared
reference gate. It reads the saved document, derives the requested comparisons, computes ad hoc comparisons in memory,
and prints the laranex-schema-gate 1.0 report or a readable summary of it.

Reads only the document: never a database, an ORM, application code or a subprocess, and never writes the document.
docs/contract.md, "Gate reports", defines the findings, errors and exit codes; docs/check.md describes the command.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import model as m
from .compare import compute, requests
from .config import ComparisonConfig
from .contract import VERSION, document_to_json, validate_document
from .contract.comparison import RequestedComparison, expand_comparison_requests, store_comparisons
from .contract.gate import RANK, UNPARSED, gate_document, qualifies
from .snapshot import MAX_SNAPSHOT_BYTES

# Expanding requests against no connections finds the errors that do not depend on the document.
NO_CONNECTIONS = m.Document(source="database", generator=m.Generator(name="laranex-schema-viewer"))


@dataclass(frozen=True)
class Input:
    """The document as the gate receives it (``UNPARSED`` when it could not be read or parsed) and, for the summary
    and stderr only, why it could not be read."""

    data: Any
    note: str | None = None


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def read_input(path: Path) -> Input:
    """The snapshot parsed as JSON, without validating it: validation belongs to the gate."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    except OSError as error:
        return Input(UNPARSED, f"Cannot read {path}: {error.strerror or error}")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        return Input(UNPARSED, f"{path} exceeds the 32 MiB limit")
    try:
        # As JSON.parse: UTF-8 text, and no NaN or Infinity.
        return Input(json.loads(raw.decode("utf-8"), parse_constant=_no_constant))
    except ValueError as error:
        return Input(UNPARSED, f"{path} is not JSON: {error}")


def with_ad_hoc(data: Any, specs: Sequence[ComparisonConfig]) -> Any:
    """The document with ``specs`` computed and stored in memory, as ``compare --write`` would store them, when it
    is a valid 2.x document. Requests that cannot be expanded are left for the gate, which expands the same requests
    and reports their errors as not found; an invalid document is left to the gate."""
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return data
    document, _ = validate_document(data, strict=data["version"] == VERSION)
    if document is None:
        return data
    stored = document_to_json(store_comparisons(document, compute(document, specs, strict=False)))
    # Only the computed arrays change: a newer minor's version and additions stay as read.
    return {**data, "comparisons": stored["comparisons"], "diagnostics": stored["diagnostics"]}


def reject_duplicates(requested: list[RequestedComparison]) -> None:
    """A comparison requested twice is a configuration error, whatever the document holds (the gate itself lists
    each request once). Every other expansion error is the gate's to report."""
    _, errors = expand_comparison_requests(NO_CONNECTIONS, requested)
    duplicates = [e.message for e in errors if e.kind == "duplicate"]
    if duplicates:
        raise ValueError("\n".join(duplicates))


def validator_errors(data: Any, limit: int = 5) -> list[str]:
    """Why a document the gate called invalid failed validation, printed beside the report, never in it."""
    if data is UNPARSED or not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return []
    _, errors = validate_document(data, strict=data["version"] == VERSION)
    return errors[:limit] + ([f"and {len(errors) - limit} more"] if len(errors) > limit else [])


def _spell(scope: dict[str, Any]) -> str:
    return scope["connection"] if scope["schema"] is None else f"{scope['connection']}:{scope['schema']}"


def summary(report: dict[str, Any]) -> str:
    """A readable, deterministic summary of a report: the verdict, what was checked, the errors and the qualifying
    findings."""
    t, counts = report["thresholds"], report["counts"]
    verdict = {0: "PASSED", 1: "FAILED", 2: "INCOMPLETE"}[report["exitCode"]]
    lines = [f"Schema check {verdict} (exit code {report['exitCode']}): {counts['qualifying']} of {counts['findings']} "
             f"findings meet the thresholds (severity {t['severity']} or above, "
             f"{'certain only' if t['certainty'] == 'certain' else 'certain or likely'})"]
    requested = ", ".join(f"{_spell(r['baseline'])} → {_spell(r['target'])}" for r in report["requested"])
    lines.append(f"Checked {len(report['comparisons'])} comparisons"
                 + (f" (requested: {requested})" if requested else " (every stored one)"))
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


def run(path: Path, thresholds: dict[str, str | None], configured: Iterable[ComparisonConfig],
        ad_hoc: Iterable[ComparisonConfig]) -> tuple[dict[str, Any], list[str]]:
    """The gate report of the snapshot at ``path``, and notes for stderr. Comparisons named on the command line
    (``ad_hoc``) are computed in memory, then requested; otherwise the configured ones are requested as stored."""
    ad_hoc = tuple(ad_hoc)
    requested = requests(ad_hoc or configured)
    reject_duplicates(requested)
    given = read_input(path)
    data = with_ad_hoc(given.data, ad_hoc) if ad_hoc else given.data
    report = gate_document(data, thresholds=thresholds, requested=requested)
    notes = [given.note] if given.note else []
    if any(e["kind"] == "invalid_document" for e in report["errors"]):
        notes += [f"{path}: {e}" for e in validator_errors(given.data)]
    return report, notes
