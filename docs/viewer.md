# Interactive ERD viewer

Open a saved document with:

```bash
laranex-schema-viewer serve --snapshot schema-viewer.json
```

Visit **http://127.0.0.1:8765**. `serve` shows the shared Laranex Schema Viewer UI (`laranex-schema-viewer-ui`), the same viewer the Laravel package uses. It is vendored prebuilt in the Python package, so no Node.js or frontend build is needed at runtime, and it loads nothing from outside the local server.

The viewer reads laranex-schema 2.x documents: declared (`build`), database (`build --source database`) and combined (`build --source combined`). The vendored build is laranex-schema-viewer-ui 0.3.0-dev.0 (commit 0902ccb), which reads laranex-schema 2.x. A file of another format or version stops `serve` with an error asking for a rebuild.

## Explore the diagram

- Drag table cards (Shift-click several to move them together), pan, zoom, fit, and arrange the layout again.
- Search tables, columns, models and relationships; browse tables, relationships or columns in the side panel.
- Select a table for its columns, primary key, indexes (unique, composite, partial with predicate, expression, method, included columns), constraints, foreign keys in both directions, ORM relationships and models.
- Focus a table's neighbors to a chosen depth; the selection is kept in the URL.
- Switch light/dark themes (remembered), and download the JSON document or a PNG/SVG image of the diagram.
- On a phone-width screen the side panel becomes a bottom sheet.

## Foreign keys and ORM relationships

Foreign-key constraints and declared ORM relationships stay separate on the diagram, in the legend and in the details. A link is drawn as a foreign key, as an ORM relationship with no constraint behind it, or as both when a relationship matches a constraint. Relationships through other tables are drawn separately. The **Relationships** toolbar menu shows or hides each kind. Selecting a link explains what it is.

Self references and composite foreign keys keep their column pairs. Relationship cardinality describes mapped behavior; it is not a database uniqueness guarantee.

## Several connections

A document built from several database connections (see [database introspection](./database.md#several-connections)) holds one connection per name, each with its own driver:

- **All connections** (the default): each connection is laid out on its own inside a labelled frame showing its name, driver and table count, so a `users` table on two connections is drawn twice, never merged. Relationships between connections run between the frames.
- **One connection**: pick it in the header's **Connection** menu (on a phone, in the ⋯ menu), or step through all connections and each connection with <kbd>[</kbd> and <kbd>]</kbd>. Tables on other connections that the shown tables' relationships reach stay on the diagram as dashed reference nodes marked *On connection NAME · driver*. They cannot be moved or folded; clicking one opens that table on its own connection.
- Every table label names its connection, for example `analytics · users`: on cards, in Browse, search results, Details headings, edge details, the phone sheet and image exports.
- A relationship between two connections is always drawn as an ORM relationship with no constraint (dashed), marked `cross-connection`. Foreign keys never cross connections.
- Deep links: `#connection=<id>` opens one connection (an unknown id opens all of them), and `#table=<id>` opens that table, also on its own connection when `#connection=` names another one. Connection ids look like `connection:analytics`; table ids are opaque and qualified by connection.
- Coverage is listed per connection, and drift and reconciliation diagnostics name their connection.

Known limitations of the shared viewer (laranex-schema-viewer-ui 0.2.0):

- Frames are placed by the automatic layout. Dragging a table does not move or resize its connection's frame, and frames themselves cannot be dragged. **Arrange again** lays out the tables and their frames afresh.
- The intermediate (`via`) tables of a relationship through other tables are not drawn as reference nodes when they sit on another connection; only the relationship's two ends are.
- Frames show the connection's name, driver and table count only; the per-connection hint counts Laravel Schema Viewer shows in its frames are not reproduced.

## What each source shows

The **Details** tab, with nothing selected, shows the document's source as a badge with a sentence about what it means, the legend, and a **Coverage** section per connection: whether the declared metadata and the database inspection are complete, and which features were only partly read or unavailable.

- **declared**: ORM metadata only, no database was inspected. Foreign keys are declared ones.
- **database**: database introspection only, with no models or ORM relationships.
- **combined**: the database is authoritative where it was inspected, enriched with models and relationships from the ORM. Coverage lists both the database and the declared side.

In a combined document the viewer also shows where the two sides disagree:

- **Provenance badges**: tables, columns, indexes, foreign keys and constraints found on one side only are marked *declared only* or *database only*. Declared-only tables, columns and foreign-key lines are drawn in the declared color.
- **Drift**: columns that differ between the two sides (for example in type, nullability or default) are tinted on the diagram and in the columns table, with a drift list giving the database and declared values. Indexes, foreign keys and tables carry their own drift lists.
- **Diagnostics**: the tab count, per-table issue counts and the Diagnostics tab list every diagnostic. Confirmed **facts** are kept apart from **heuristics** (comparisons that rely on normalized type names or SQL text), and a diagnostic about a table links to it.

## Comparisons, migrations and inferred associations

- **Compare**: when the document stores [schema comparisons](./comparison.md), the Compare tab shows one baseline → target pair at a time: the summary, every listed difference with its baseline and target values side by side, filters by object, change, severity and certainty, and a prominent note when the comparison is incomplete. **Show in baseline/target** opens the difference's table, and the diagram can mark the differences on table cards. The view is kept in the URL: `#view=compare&comparison=<id>&difference=<id>`.
- **Migrations**: each stored migration track's revision status, kept apart from schema drift.
- **Inferred associations**: foreign-key edges an inferred association uses are annotated as inferred; nothing is drawn that the document does not link.

This package stores comparisons; it writes migration tracks and inferred associations empty, so those two views show only documents from other producers.

## Current boundaries

The viewer is read-only. It does not edit schemas, persist layouts, connect to a database or generate migrations. Rebuild the document when models or the database change.

Try the [interactive demo](./demo.md).
