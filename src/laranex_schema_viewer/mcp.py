"""MCP SDK stdio transport over one saved laranex-schema 2.x document. Only that document is accessible.

The responses are query.py's; docs/mcp.md specifies them."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from .query import SnapshotQuery

Limit = Annotated[int, Field(ge=1, le=100, strict=True)]
Offset = Annotated[int, Field(ge=0, strict=True)]


def create_server(path: str | Path):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp_types import ToolAnnotations

    query = SnapshotQuery(path)
    server = MCPServer("Laranex Schema Viewer")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=False)

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ValueError as error:
            raise ToolError(str(error)) from None

    @server.tool(annotations=annotations)
    def schema_overview(limit: Limit = 25, offset: Offset = 0, revision: str | None = None) -> dict:
        """Document source, connections with coverage, counts, and paginated table summaries with opaque IDs."""
        return invoke(query.overview, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_search(query_text: str, limit: Limit = 25, offset: Offset = 0,
                      revision: str | None = None) -> dict:
        """Literal case-insensitive search of table, column, index, foreign key, constraint, model and relationship
        names; returns hits with opaque IDs."""
        return invoke(query.search, query_text, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_table(table: str,
                         section: Literal["columns", "indexes", "foreign_keys", "constraints", "models",
                                          "relationships", "links", "derived_links", "diagnostics"] = "columns",
                         limit: Limit = 25, offset: Offset = 0, revision: str | None = None) -> dict:
        """Read one section of a table, as the document's own objects, by the table's exact opaque ID."""
        return invoke(query.get_table, table, section, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_model(model: str, limit: Limit = 25, offset: Offset = 0,
                         revision: str | None = None) -> dict:
        """Read a model and the ORM relationships it declares, by exact model ID."""
        return invoke(query.get_model, model, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_relationships(table: str | None = None,
                                 kind: Literal["orm", "foreign_key"] = "orm",
                                 limit: Limit = 25, offset: Offset = 0,
                                 revision: str | None = None) -> dict:
        """Read declared ORM relationships (kind orm) or physical foreign keys (kind foreign_key), never mixed."""
        return invoke(query.relationships, table, kind, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_compare(section: Literal["comparisons", "differences"] = "comparisons",
                       comparison: str | None = None, baseline: str | None = None, target: str | None = None,
                       object_type: Literal["table", "column", "primaryKey", "index", "foreignKey",
                                            "constraint"] | None = None,
                       change: Literal["missing", "extra", "changed"] | None = None,
                       severity: Literal["error", "warning", "info"] | None = None,
                       certainty: Literal["certain", "likely"] | None = None,
                       limit: Limit = 25, offset: Offset = 0, revision: str | None = None) -> dict:
        """Read the schema comparisons stored in the document (section comparisons) or their differences (section
        differences), by comparison ID or by baseline/target scope (CONNECTION or CONNECTION:SCHEMA), and filter
        differences by object_type, change, severity and certainty. Never computes or connects."""
        return invoke(query.compare, section, comparison, baseline, target, object_type, change, severity, certainty,
                      limit=limit, offset=offset, revision=revision)

    return server

