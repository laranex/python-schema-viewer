"""The laranex-schema 2.0 contract shared with laranex-schema-viewer-ui: canonical serialization of the schema model
(serialize.py), validation (shape.py, validate.py), the graph builder and canonical form (graph.py), ids (ids.py) and
migration status rules (migration.py). Never imports an ORM or a database driver."""

from .graph import build_graph, finalize, in_scope
from .ids import derived_link_id, diagnostic_id, link_id
from .serialize import (FORMAT, MAJOR, VERSION, canonical_json, document_bytes, document_from_json, document_to_json,
                        from_json, revision, to_json)
from .validate import (InvalidDocumentError, UnsupportedDocumentError, assert_document, supported,
                       validate_document)

__all__ = [
    "FORMAT", "MAJOR", "VERSION", "InvalidDocumentError", "UnsupportedDocumentError", "assert_document",
    "build_graph", "canonical_json", "derived_link_id", "diagnostic_id", "document_bytes", "document_from_json",
    "document_to_json", "finalize", "from_json", "in_scope", "link_id", "revision", "supported", "to_json",
    "validate_document",
]
