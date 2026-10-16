"""Adapters only run while building. Future adapters implement SchemaAdapter."""

from typing import Protocol

from ..document import SchemaDocument


class SchemaAdapter(Protocol):
    def extract(self) -> SchemaDocument: ...

