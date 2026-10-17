# Architecture

Application definitions → build-only loader → ORM adapter → validated document → JSON snapshot → viewer / MCP.

The dependency boundary is intentional. `document.py` defines ORM-neutral Pydantic value objects; `snapshot.py` validates and persists them. Neither imports an ORM. `build.py` owns all explicit and recursive application imports and selects an adapter: SQLAlchemy (shared with SQLModel), Tortoise, and Peewee are detected from the imported modules without importing other ORMs; Django is selected by a settings module because `django.setup()` must run before its models import. The CLI imports each command's implementation lazily. Tests block ORM/build imports while constructing the viewer/MCP paths.

Each adapter module in `adapters/` provides `defines_models(loaded)` and `document_from_modules(loaded, project_root=..., targets=...)` and is registered in `build.ADAPTERS`; its adapter class implements `SchemaAdapter.extract() -> SchemaDocument`. Keep ORM dependencies optional and imported only by their adapter. The viewer and MCP query layer consume the same contract without ORM-specific branches.

## Document design

Tables have metadata namespace, schema, and name identities. Namespaces come from explicit API keys or deterministic loader discovery; a single metadata object uses `default`. Provide explicit namespace keys through the adapter API if stable external identities across application reorganizations are required. Separate metadata objects never share an identity solely because their tables have the same name.

Foreign keys belong to their source table and preserve ordered source/target columns. Relationships are separate top-level declarations, including both directions of a back-populated pair. The graph builder does not merge or infer them. Many-to-many relationships retain their secondary table and join expressions. Scalar/collection behavior is not treated as proof of database uniqueness. Primary keys, unique constraints, and partial uniqueness remain separately inspectable.

Snapshot version 1 is specific to this Python package; it is not wire-compatible with the Laravel snapshot. The viewer is the shared laranex-schema-viewer-ui, which reads laranex-schema documents only; `serve` converts a version 1 snapshot with `unified.from_v1` on each request. Its prebuilt bundle is vendored in `static/viewer/` by `scripts/sync_viewer.py` (checked against the build's manifest), so the package ships no Node.js runtime or build step and the viewer stays independent of extraction and querying.

Only source paths inside an explicitly selected project root are recorded, as relative paths. Callable defaults are represented by qualified names. SQL expressions are descriptive text, never evaluated by snapshot consumers. Unsupported secondary selectables and unresolved foreign keys produce diagnostics. Broken mapper configuration aborts extraction instead of silently dropping relationships.

The writer validates before an atomic replacement, sorts unordered metadata collections, omits timestamps, and preserves declared column and key order. The reader limits snapshots to 32 MiB and rejects invalid/unknown versions and invalid references. MCP pins a document revision, paginates at 25 items by default (maximum 100), and bounds query responses to 64 KiB.

## Serving and isolation

The HTTP server is a local development server bound to `127.0.0.1` by default. It serves only the packaged viewer files its manifest lists and the configured snapshot; there is no project-directory file serving. It reloads and validates the snapshot on each request. Responses use a content security policy, prohibit framing, and do not enable CORS. Binding another interface is explicit; the MVP does not provide authentication or production hosting.

MCP runs over stdio using the official SDK 2.x, with read-only tool annotations. Its query service owns one validated saved snapshot. Tool callers cannot select filesystem paths, refresh models, rebuild, run SQL, or mutate the document. A restart loads a newly built snapshot. Stdio stdout is reserved for protocol messages; the SDK uses stderr for logging.

`build` imports application code in a separate extraction worker (`isolation/`): a subprocess with an allowlisted environment, a timeout and process-group cleanup, or a container with no network and a read-only project. Inside the worker, audit hooks and ORM connect patches refuse network, process, file-write and database operations. These guards are best-effort, not a boundary. [Build security](./security.md) separates the guarantees from the safeguards. Within the worker, import isolation restores `sys.path` and suppresses `.pyc` writes. Mapper configuration may execute user callbacks and deferred join expressions. No default callable, model constructor, session, migration, or engine is invoked by the extractor itself.

## MVP limitations

- Extracts declared definitions, without live reflection, migration drift checks, tenant enumeration, or inferred polymorphic links.
- Ordinary mapped tables, association tables, and standard declarative inheritance are the target. Arbitrary ORM mappings to joins/subqueries can fail explicitly; they are not fully supported.
- Custom join/secondary selectables retain SQL text and diagnostics where column identities cannot be resolved. Edges whose foreign-key target is absent from metadata cannot be drawn.
- Django types come from the configured backend's `db_type()`, Tortoise types from its field SQL types for the configured or generic dialect, and Peewee types from the model database's DDL context. Django connections are blocked during extraction.
- Column SQL types are rendered through SQLAlchemy's generic compiler, with an explicit optional timezone flag because generic date/time strings omit it. Some dialect/custom types may need a future type serializer. Generated defaults identify their kind/expression, without every dialect-specific identity option.
- Index expressions, order, uniqueness, predicates, INCLUDE columns, and methods are captured; dialect operator classes and storage options are not yet normalized.
- No layout persistence, schema diff, hint engine, ASGI mounting, or custom framework integrations yet.
- Python 3.10+ is the intended package range; only the locally available Python 3.15 runtime was executed in this session. CI defines the broader matrix.

