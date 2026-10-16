---
aside: false
---

# Explore a sample schema

An invented project workspace with nine tables, foreign keys, self references, association tables, indexes, and ORM relationships. This demo runs entirely in your browser from a synthetic JSON snapshot.

<LiveDemo />

## Things to try

1. Search for `tasks` and select the table to inspect its columns and indexes.
2. Toggle the foreign-key and ORM layers to compare constraints with declared relationships.
3. Follow `users.manager_id` to see a self reference.
4. Explore `task_tags` to see a physical association table and ORM paths.
5. Zoom, pan, drag cards, and fit the graph back into view.

The demo reuses the package's actual viewer assets. It does not load a real application's schema, credentials, database, or data rows.
