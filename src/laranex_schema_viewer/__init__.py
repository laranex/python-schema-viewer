"""Saved laranex-schema 2.0 documents for people and agents. Application loading is build-only."""

from .model import Document
from .snapshot import Snapshot, read_document, write_document

__all__ = ["Document", "Snapshot", "read_document", "write_document"]
__version__ = "0.6.0"
