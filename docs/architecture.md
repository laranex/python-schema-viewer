# Architecture

Application definitions → build-only loader → ORM adapter → validated document → JSON snapshot → viewer / MCP.

The dependency boundary is intentional. `document.py` defines ORM-neutral Pydantic value objects; `snapshot.py` validates and persists them. Neither imports SQLAlchemy. `build.py` owns all explicit and recursive application imports. The SQLAlchemy adapter uses metadata, mapper, and relationship APIs, shared with SQLModel. The CLI imports each command's implementation lazily. Tests block ORM/build imports while constructing the viewer/MCP paths.

`SchemaAdapter.extract() -> SchemaDocument` is the extension point for a future ORM. Implement another adapter and a build entry point; keep its dependencies optional. The viewer and MCP query layer consume the same contract without ORM-specific branches.

## Document design

Tables have metadata namespace, schema, and name identities. Namespaces come from explicit API keys or deterministic loader discovery; a single metadata object uses `default`. Provide explicit namespace keys through the adapter API if stable external identities across application reorganizations are required. Separate metadata objects never share an identity solely because their tables have the same name.

Foreign keys belong to their source table and preserve ordered source/target columns. Relationships are separate top-level declarations, including both directions of a back-populated pair. The graph builder does not merge or infer them. Many-to-many relationships retain their secondary table and join expressions. Scalar/collection behavior is not treated as proof of database uniqueness. Primary keys, unique constraints, and partial uniqueness remain separately inspectable.

Snapshot version 1 is specific to this Python package; it is not wire-compatible with the Laravel snapshot. The UI reuses the Laravel visual design rather than populating Eloquent-specific fields with fake values. A compact HTML/SVG implementation avoids shipping React/ELK dependencies and frontend build tooling for this MVP. Its pure graph module is replaceable independently of extraction and querying.

Only source paths inside an explicitly selected project root are recorded, as relative paths. Callable defaults are represented by qualified names. SQL expressions are descriptive text, never evaluated by snapshot consumers. Unsupported secondary selectables and unresolved foreign keys produce diagnostics. Broken mapper configuration aborts extraction instead of silently dropping relationships.

The writer validates before an atomic replacement, sorts unordered metadata collections, omits timestamps, and preserves declared column and key order. The reader limits snapshots to 32 MiB and rejects invalid/unknown versions and invalid references. MCP pins a document revision, paginates at 25 items by default (maximum 100), and bounds query responses to 64 KiB.

## Serving and isolation

The HTTP server is a local development server bound to `127.0.0.1` by default. It serves only explicitly allowlisted packaged assets and the configured snapshot; there is no project-directory file serving. It reloads and validates the snapshot on each request. Responses use a content security policy, prohibit framing, and do not enable CORS. Binding another interface is explicit; the MVP does not provide authentication or production hosting.

MCP runs over stdio using the official SDK 2.x, with read-only tool annotations. Its query service owns one validated saved snapshot. Tool callers cannot select filesystem paths, refresh models, rebuild, run SQL, or mutate the document. A restart loads a newly built snapshot. Stdio stdout is reserved for protocol messages; the SDK uses stderr for logging.

Import isolation restores `sys.path` and suppresses `.pyc` writes during loading, but cannot undo arbitrary application import effects. It is not a sandbox. CLI builds should run in fresh processes. Mapper configuration may execute user callbacks and deferred join expressions. No default callable, model constructor, session, migration, or engine is invoked by the extractor itself.

## MVP limitations

- Extracts declared definitions, without live reflection, migration drift checks, tenant enumeration, or inferred polymorphic links.
- Ordinary mapped tables, association tables, and standard declarative inheritance are the target. Arbitrary ORM mappings to joins/subqueries can fail explicitly; they are not fully supported.
- Custom join/secondary selectables retain SQL text and diagnostics where column identities cannot be resolved. Edges whose foreign-key target is absent from metadata cannot be drawn.
- Column SQL types are rendered through SQLAlchemy's generic compiler, with an explicit optional timezone flag because generic date/time strings omit it. Some dialect/custom types may need a future type serializer. Generated defaults identify their kind/expression, without every dialect-specific identity option.
- Index expressions, order, uniqueness, predicates, INCLUDE columns, and methods are captured; dialect operator classes and storage options are not yet normalized.
- The ERD uses a deterministic neighborhood-oriented grid, rather than ELK routing. Large dense graphs can have crossing edges. Composite FK details retain all pairs; the graph anchors its edge to the first pair.
- No layout persistence, diagram image exports, schema diff, hint engine, ASGI mounting, or custom framework integrations yet.
- Python 3.10+ is the intended package range; only the locally available Python 3.15 runtime was executed in this session. CI defines the broader matrix.

