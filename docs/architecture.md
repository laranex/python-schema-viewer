# Architecture

Application definitions or database catalogs → extraction → schema model → laranex-schema 2.0 JSON → viewer / MCP.

The package keeps three layers apart, so that only one of them knows the document's field names:

1. **Extraction.** `adapters/` has one module per ORM (SQLAlchemy, shared with SQLModel; Django; Tortoise ORM; Peewee); `introspection/catalog.py` reads a database's catalogs. Both build the schema model and never write JSON. `adapters/__init__.py` holds the ORM-neutral helpers every adapter shares (index parts, defaults, referential actions, relationship keys and pivots, declared tables and documents).
2. **The schema model** (`model.py`): frozen dataclasses with Python attribute names (`foreign_columns`, `primary_key`, `orm_default`), independent of the JSON. Reconciliation (`reconcile.py`) and the MCP query service (`query.py`) work on it.
3. **The contract** (`contract/`): `serialize.py` maps the model to and from laranex-schema 2.0 JSON (camelCase, canonical key order) and is the only code that knows its field names; `shape.py` and `validate.py` port the shared validator, with its messages; `graph.py` builds links with the shared graph rules and puts a document in canonical form (`finalize`: sorted arrays, one entry per diagnostic id); `ids.py` and `migration.py` hold the id and migration status rules.

Nothing imports an ORM outside its adapter. `build.py` owns all explicit and recursive application imports and selects an adapter: SQLAlchemy, Tortoise and Peewee are detected from the imported modules without importing other ORMs; Django is selected by a settings module because `django.setup()` must run before its models import. The CLI imports each command's implementation lazily. Tests block ORM and build imports while constructing the viewer and MCP paths.

Each adapter module provides `defines_models(loaded)` and `document_from_modules(loaded, project_root=..., targets=...)` (Django: `document_from_settings`) returning a `model.Document`, and is registered in `build.ADAPTERS`. Keep ORM dependencies optional and imported only by their adapter. The viewer and MCP query layer read the same contract without ORM-specific branches.

## The laranex-schema 2.0 document

Every build writes a [laranex-schema 2.0](https://laranex.github.io/schema-viewer-ui/contract) document, the contract shared with the Laravel and Node.js schema viewers and their viewer UI. The package reads and writes nothing else: `serve` and `mcp` accept 2.x documents only (a later minor is read leniently, ignoring what it does not know), and refuse any other format or major version with an error asking for a rebuild. The shared fixtures and conformance cases are vendored in `tests/fixtures/contract` by `scripts/sync_contract.py`, with a manifest of their source commit and hashes; the Python validator, serializer, graph builder and migration rules are checked against them independently of the TypeScript implementation.

- **Connections.** A declared document has one connection per ORM namespace (the metadata, app label or `default`), with no driver; a database document has one per inspected connection, with its canonical driver (`postgresql`, `mysql`, `mariadb`, `sqlite`) and default schema. Each records what its sources covered. Table ids are opaque and unique across the document; separate metadata objects never share an identity because their tables have the same name.
- **Physical structures and ORM declarations stay apart.** Foreign keys belong to their table and keep ordered column pairs; indexes keep every key part (column or expression, order, length, collation, operator class) and what they back; ORM relationships are top-level, with keys oriented like a foreign key, pivots, joins and inverses. `links` relate the two by the shared graph rules and never merge them. A one-to-one cardinality is not treated as proof of database uniqueness.
- **Diagnostics** have ids computed from their content, a category (`extraction`, `inspection`, `reconciliation`, `drift`, `lint`), a severity and a certainty (`likely` for comparisons that rest on normalized type or SQL text).
- **Comparisons, migrations and inferred associations** are required arrays that this version leaves empty; computing them is planned. When migration tracks are produced, `diverged` will mean exactly what the contract says: one current revision in the version table is an ancestor of another (overlapping revision entries), so the database's position cannot be determined. That is different from branch divergence, several unmerged heads in the migration scripts, which is `history.heads` with more than one entry and a `migration_multiple_heads` lint diagnostic. Inferred associations will be heuristics: their confidence, even `high`, never implies that an ORM relationship attribute exists.

Only source paths inside an explicitly selected project root are recorded, as relative paths. Callable defaults are represented by qualified names and never called. SQL expressions are descriptive text, never evaluated by readers. Unsupported declarations, secondary selectables and unresolved foreign keys produce diagnostics. Broken mapper configuration aborts extraction instead of silently dropping relationships.

The writer validates strictly before an atomic replacement, writes canonical JSON without timestamps, and keeps declared column and key order. The reader limits documents to 32 MiB. MCP pins a document revision (the SHA-256 of its canonical bytes), paginates at 25 items by default (maximum 100), and bounds responses to 64 KiB.

## Serving and isolation

The HTTP server is a local development server bound to `127.0.0.1` by default. It serves only the packaged viewer files its manifest lists and the configured document; there is no project-directory file serving. It reloads and validates the document on each request. The viewer is the shared laranex-schema-viewer-ui, vendored prebuilt in `static/viewer/` by `scripts/sync_viewer.py` (checked against the build's manifest), so the package ships no Node.js runtime or build step and the viewer stays independent of extraction and querying. Responses use a content security policy, prohibit framing, and do not enable CORS. Binding another interface is explicit; the MVP does not provide authentication or production hosting.

MCP runs over stdio using the official SDK 2.x, with read-only tool annotations. Its query service owns one validated saved document. Tool callers cannot select filesystem paths, refresh models, rebuild, run SQL, or mutate the document. A restart loads a newly built document. Stdio stdout is reserved for protocol messages; the SDK uses stderr for logging.

`build` imports application code in a separate extraction worker (`isolation/`), which returns the declared document as laranex-schema 2.0 JSON that the parent validates strictly before using it: a subprocess with an allowlisted environment, a timeout and process-group cleanup, or a container with no network and a read-only project. Inside the worker, audit hooks and ORM connect patches refuse network, process, file-write and database operations. These guards are best-effort, not a boundary. [Build security](./security.md) separates the guarantees from the safeguards. Within the worker, import isolation restores `sys.path` and suppresses `.pyc` writes. Mapper configuration may execute user callbacks and deferred join expressions. No default callable, model constructor, session, migration, or engine is invoked by the extractor itself.

## MVP limitations

- Declared builds extract definitions; database and combined builds read catalogs read-only. There are no migration status checks, schema comparisons, inferred associations, tenant enumeration, or inferred polymorphic links yet.
- Ordinary mapped tables, association tables, and standard declarative inheritance are the target. Arbitrary ORM mappings to joins/subqueries can fail explicitly; they are not fully supported.
- Custom join/secondary selectables retain SQL text and diagnostics where column identities cannot be resolved. Edges whose foreign-key target is absent from metadata cannot be drawn.
- Django types come from the configured backend's `db_type()`, Tortoise types from its field SQL types for the configured or generic dialect, and Peewee types from the model database's DDL context. Django connections are blocked during extraction.
- Column SQL types are rendered through SQLAlchemy's generic compiler, with an explicit optional timezone flag because generic date/time strings omit it. Some dialect/custom types may need a future type serializer. Generated defaults identify their kind/expression, without every dialect-specific identity option.
- Index expressions, order, uniqueness, predicates, INCLUDE columns, and methods are captured; dialect operator classes and storage options are not yet normalized.
- No layout persistence, schema diff, hint engine, ASGI mounting, or custom framework integrations yet.
- Python 3.10+ is the intended package range; only the locally available Python 3.15 runtime was executed in this session. CI defines the broader matrix.

