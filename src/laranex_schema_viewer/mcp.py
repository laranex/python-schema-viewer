"""MCP SDK stdio transport. Only the configured snapshot is accessible."""

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
        """Schema counts and paginated table IDs. Reads a pinned saved snapshot only."""
        return invoke(query.overview, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_search(query_text: str, limit: Limit = 25, offset: Offset = 0,
                      revision: str | None = None) -> dict:
        """Literal substring search of table, column, model and declared relationship names."""
        return invoke(query.search, query_text, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_table(table: str,
                         section: Literal["columns", "indexes", "foreign_keys", "constraints",
                                          "models", "relationships"] = "columns",
                         limit: Limit = 25, offset: Offset = 0, revision: str | None = None) -> dict:
        """Read a table section using its exact opaque ID from overview/search."""
        return invoke(query.get_table, table, section, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_model(model: str, limit: Limit = 25, offset: Offset = 0,
                         revision: str | None = None) -> dict:
        """Read model metadata and its paginated declared ORM relationships by exact model ID."""
        return invoke(query.get_model, model, limit=limit, offset=offset, revision=revision)

    @server.tool(annotations=annotations)
    def schema_get_relationships(table: str | None = None,
                                 kind: Literal["orm", "foreign_key"] = "orm",
                                 limit: Limit = 25, offset: Offset = 0,
                                 revision: str | None = None) -> dict:
        """Read declared ORM relationships or physical foreign keys as separate collections."""
        return invoke(query.relationships, table, kind, limit=limit, offset=offset, revision=revision)

    return server

