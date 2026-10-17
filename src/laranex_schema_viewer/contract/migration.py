"""Migration history and status rules (docs/contract.md, "Migrations"), a port of laranex-schema-viewer-ui's
migration.ts checked against contract/conformance/migrations. The validator recomputes every track's status with them.

Two different things are called divergence; keep them apart:

- status ``diverged``: the version table records overlapping revisions, one current revision being an ancestor of
  another (for example ``a`` and ``c`` recorded for the history ``a <- b <- c``). The database's position cannot be
  determined, so ``pending`` is null.
- branch divergence: the migration scripts have several heads (unmerged branches). That is ``history.heads`` with more
  than one entry, reported by a ``migration_multiple_heads`` lint diagnostic. A database at every head is
  ``up_to_date``; one missing a branch's revisions is ``behind``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..model import MigrationHistory, MigrationRevision
from .ids import sorted_strings


def migration_history(revisions: Iterable[MigrationRevision]) -> MigrationHistory:
    """A history from its revisions: sorted, with heads (named by no revision as a parent) and missing parents."""
    ordered = sorted((MigrationRevision(id=r.id, parents=tuple(sorted_strings(r.parents)),
                                        branch_labels=tuple(sorted_strings(r.branch_labels))) for r in revisions),
                     key=lambda r: r.id)
    ids = {r.id for r in ordered}
    parents = {p for r in ordered for p in r.parents}
    return MigrationHistory(revisions=tuple(ordered),
                            heads=tuple(sorted_strings(r.id for r in ordered if r.id not in parents)),
                            missing=tuple(sorted_strings(p for p in parents if p not in ids)))


@dataclass(frozen=True)
class MigrationState:
    status: str
    pending: tuple[str, ...] | None
    unknown_revisions: tuple[str, ...] = ()


def migration_state(history: MigrationHistory | None, current: Sequence[str] | None) -> MigrationState:
    """The status of a database at revisions ``current`` against a history, by the contract's ordered rules."""
    if current is None:
        return MigrationState("unknown", None)
    parents_of = {r.id: r.parents for r in (history.revisions if history else ())}

    def ancestry(start: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        stack = list(start)
        while stack:
            revision = stack.pop()
            if revision not in seen:
                seen.add(revision)
                stack.extend(parents_of.get(revision, ()))
        return seen

    missing = set(history.missing if history else ())

    def pending_from(applied: set[str]) -> tuple[str, ...] | None:
        pending = [r for r in ancestry(history.heads if history else ()) if r not in applied]
        return None if any(r in missing for r in pending) else tuple(sorted_strings(pending))

    if not current:
        return MigrationState("uninitialized", None if history is None else pending_from(set()))
    if history is None:
        return MigrationState("unknown", None)
    unknown = tuple(sorted_strings(r for r in current if r not in parents_of))
    if unknown:
        return MigrationState("unknown", None, unknown)
    if any(other != c and c in ancestry(parents_of.get(other, ())) for c in current for other in current):
        return MigrationState("diverged", None)
    pending = pending_from(ancestry(current))
    if pending is None:
        return MigrationState("unknown", None)
    return MigrationState("behind" if pending else "up_to_date", pending)
