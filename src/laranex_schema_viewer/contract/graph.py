"""The framework-neutral graph builder (docs/contract.md, "Graph"), a port of laranex-schema-viewer-ui's graph.ts.

The shared conformance cases (tests/fixtures/contract/conformance/graph) keep the two implementations identical.
"""

from __future__ import annotations

from typing import Any

from .serialize import hash_id


def link_id(from_table: str, to_table: str, pairs: list[list[str]]) -> str:
    return hash_id("l", ["link", from_table, to_table, pairs])


def derived_link_id(relationship: str) -> str:
    return hash_id("d", ["derived", relationship])


def _pairs(source: dict[str, Any], target: dict[str, Any]) -> list[list[str]]:
    return sorted([[c, target["columns"][i]] for i, c in enumerate(source["columns"])])


def build_graph(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Links and derived links from a document's JSON data: connections and relationships."""
    columns = {t["id"]: {c["name"] for c in t["columns"]}
               for connection in document["connections"] for t in connection["tables"]}

    def resolved(endpoint: dict[str, Any]) -> bool:
        return bool(endpoint["columns"]) and all(c in columns.get(endpoint["table"], ()) for c in endpoint["columns"])

    links: dict[str, dict[str, Any]] = {}
    notes: dict[str, list[tuple[str, str]]] = {}

    def add(source: dict[str, Any], target: dict[str, Any], foreign_key: str | None = None,
            relationship: dict[str, Any] | None = None) -> None:
        if not resolved(source) or not resolved(target) or len(source["columns"]) != len(target["columns"]):
            return
        lid = link_id(source["table"], target["table"], _pairs(source, target))
        link = links.get(lid)
        if link is None:
            link = links[lid] = {"id": lid, "from": source, "to": target, "foreignKey": None, "relationships": [],
                                 "state": "relationship", "polymorphic": False, "note": None}
            notes[lid] = []
        if foreign_key is not None and (link["foreignKey"] is None or foreign_key < link["foreignKey"]):
            # Duplicate constraints on the same columns: the smallest id stands for them.
            link.update(foreignKey=foreign_key, **{"from": source, "to": target})
        if relationship is not None and relationship["id"] not in link["relationships"]:
            link["relationships"].append(relationship["id"])
            link["polymorphic"] = link["polymorphic"] or relationship.get("polymorphic") is not None
            if relationship.get("note"):
                notes[lid].append((relationship["id"], relationship["note"]))

    for connection in document["connections"]:
        for table in connection["tables"]:
            for fk in sorted(table["foreignKeys"], key=lambda f: f["id"]):
                add({"table": table["id"], "columns": fk["columns"]},
                    {"table": fk["foreignTable"], "columns": fk["foreignColumns"]}, foreign_key=fk["id"])

    tables = set(columns)
    derived = []
    for r in document["relationships"]:
        if r.get("through"):
            if r["relatedTable"] is not None and {r["table"], r["relatedTable"], *r["through"]} <= tables:
                derived.append({"id": derived_link_id(r["id"]), "from": r["table"], "to": r["relatedTable"],
                                "via": r["through"], "relationship": r["id"]})
        elif r.get("pivot"):
            p = r["pivot"]
            add({"table": p["table"], "columns": p["parentPivotColumns"]},
                {"table": r["table"], "columns": p["parentColumns"]}, relationship=r)
            if r["relatedTable"] is not None:
                add({"table": p["table"], "columns": p["relatedPivotColumns"]},
                    {"table": r["relatedTable"], "columns": p["relatedColumns"]}, relationship=r)
        elif r.get("keys"):
            add(r["keys"]["from"], r["keys"]["to"], relationship=r)

    result = []
    for lid in sorted(links):
        link = links[lid]
        link["relationships"].sort()
        link["state"] = (("both" if link["relationships"] else "foreign_key") if link["foreignKey"] is not None
                         else "relationship")
        link["note"] = min(notes[lid])[1] if notes[lid] else None
        result.append(link)
    return {"links": result, "derivedLinks": sorted(derived, key=lambda d: d["id"])}
