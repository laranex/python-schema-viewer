# Interactive ERD viewer

Open a saved snapshot with:

```bash
laranex-schema-viewer serve --snapshot schema-viewer.json
```

Visit **http://127.0.0.1:8765**. `serve` shows the shared Laranex Schema Viewer UI (`laranex-schema-viewer-ui`), the same viewer the Laravel package uses. It is vendored prebuilt in the Python package, so no Node.js or frontend build is needed at runtime, and it loads nothing from outside the local server.

The viewer reads laranex-schema documents: declared (`build`), database (`build --source database`) and combined (`build --source combined`). A version 1 snapshot is converted on each request, exactly as `convert` would.

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

## What each source shows

The **Details** tab, with nothing selected, shows the document's source as a badge with a sentence about what it means, the legend, and a **Coverage** section per connection: whether the declared metadata and the database inspection are complete, and which features were only partly read or unavailable.

- **declared**: ORM metadata only, no database was inspected. Foreign keys are declared ones.
- **database**: database introspection only, with no models or ORM relationships.
- **combined**: the database is authoritative where it was inspected, enriched with models and relationships from the ORM. Coverage lists both the database and the declared side.

In a combined document the viewer also shows where the two sides disagree:

- **Provenance badges**: tables, columns, indexes, foreign keys and constraints found on one side only are marked *declared only* or *database only*. Declared-only tables, columns and foreign-key lines are drawn in the declared color.
- **Drift**: columns that differ between the two sides (for example in type, nullability or default) are tinted on the diagram and in the columns table, with a drift list giving the database and declared values. Indexes, foreign keys and tables carry their own drift lists.
- **Diagnostics**: the tab count, per-table issue counts and the Diagnostics tab list every diagnostic. Confirmed **facts** are kept apart from **heuristics** (comparisons that rely on normalized type names or SQL text), and a diagnostic about a table links to it.

## Current boundaries

The viewer is read-only. It does not edit schemas, persist layouts, connect to a database or generate migrations. Rebuild the snapshot when models or the database change.

Try the [interactive demo](./demo.md).
