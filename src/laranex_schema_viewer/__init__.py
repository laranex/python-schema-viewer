"""Saved schema documents for people and agents. Application loading is build-only."""

from .document import SchemaDocument
from .snapshot import read_snapshot, write_snapshot

__all__ = ["SchemaDocument", "read_snapshot", "write_snapshot"]
__version__ = "0.1.0"

