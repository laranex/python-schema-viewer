# Interactive ERD viewer

Open a saved snapshot with:

```bash
laranex-schema-viewer serve --snapshot schema-viewer.json
```

Visit **http://127.0.0.1:8765**. No frontend build is needed to run the Python package's viewer.

## Explore the graph

- Drag table cards to adjust the layout; pan the canvas and scroll to zoom.
- Fit or arrange the graph to regain context.
- Search table, column, and model names.
- Select a table for columns, keys, indexes, constraints, and relationship details.
- Focus a table's neighbors or toggle all columns for more detail.
- Switch light/dark themes or download the JSON snapshot.

## Two independent connection layers

**Foreign keys** are physical constraints, rendered as solid edges from referencing table to referenced table. They appear even without ORM relationship declarations.

**ORM relationships** are declared model attributes, rendered as dashed edges. An association-table relationship can use two segments through the secondary table. Reciprocal declarations remain distinct.

Self references and composite foreign keys retain their endpoints and column pairs. Relationship cardinality describes mapped behavior; it does not establish an unconditional database uniqueness guarantee.

## Current boundaries

The viewer is read-only. The MVP does not include schema editing, persisted layouts, live database reflection, or migration generation. Rebuild the snapshot when model definitions change.

Try the [interactive demo](./demo.md).
