import assert from 'node:assert/strict';
import {test} from 'node:test';
import {edgesFor, neighbors, matchesTable, layoutTables, edgePath} from '../src/laranex_schema_viewer/static/graph.js';

const doc = {
    tables: [
        {id: 'a', name: 'users', namespace: 'default', schema_name: null, columns: [{name: 'email'}],
            foreign_keys: [{id: 'fk1', columns: ['team_id'], target_table: 'b', target_columns: ['id']}]},
        {id: 'b', name: 'teams', namespace: 'default', columns: [{name: 'id'}], foreign_keys: []},
        {id: 'pivot', name: 'members', namespace: 'default', columns: [], foreign_keys: []},
    ],
    relationships: [
        {id: 'orm1', name: 'team', source_table: 'a', target_table: 'b',
            direction: 'MANYTOONE', join_pairs: [], secondary: null},
        {id: 'orm2', name: 'teams', source_table: 'a', target_table: 'b',
            direction: 'MANYTOMANY', join_pairs: [], secondary: 'pivot'},
        {id: 'self', name: 'manager', source_table: 'a', target_table: 'a',
            direction: 'MANYTOONE', join_pairs: [], secondary: null},
    ],
    models: [{class_name: 'app.User', table_ids: ['a']}],
};

test('FK and ORM declarations on the same tables produce distinct edges', () => {
    const edges = edgesFor(doc);
    assert.equal(edges.filter(e => e.source === 'a' && e.target === 'b').length, 2);
    assert.equal(edges.filter(e => e.kind === 'foreign_key').length, 1);
    assert.equal(new Set(edges.map(e => e.id)).size, edges.length);
});
test('many-to-many ORM edges traverse their association table', () => {
    const edges = edgesFor(doc);
    assert.ok(edges.some(e => e.id === 'orm2:source' && e.target === 'pivot'));
    assert.ok(edges.some(e => e.id === 'orm2:target' && e.source === 'pivot'));
});
test('self relationships and neighborhood focus are retained', () => {
    const edges = edgesFor(doc);
    assert.ok(edges.some(e => e.id === 'self' && e.source === e.target));
    assert.deepEqual(neighbors('a', edges), new Set(['a', 'b', 'pivot']));
});
test('focus retains the complete many-to-many declaration path', () => {
    const edges = edgesFor({...doc, tables: doc.tables.map(t => ({...t, foreign_keys: []})),
        relationships: doc.relationships.filter(r => r.id === 'orm2')});
    assert.deepEqual(neighbors('a', edges), new Set(['a', 'pivot', 'b']));
});
test('case-insensitive search covers models and columns', () => {
    assert.equal(matchesTable(doc.tables[0], 'EMAIL', doc.models), true);
    assert.equal(matchesTable(doc.tables[0], 'app.user', doc.models), true);
    assert.equal(matchesTable(doc.tables[1], 'absent', doc.models), false);
});
test('layout is deterministic and table rectangles do not overlap', () => {
    const heights = new Map(doc.tables.map(t => [t.id, 200]));
    const layout = layoutTables(doc.tables, edgesFor(doc), heights);
    assert.deepEqual(layout, layoutTables(doc.tables, edgesFor(doc), heights));
    for (const [id, p] of layout) for (const [other, q] of layout) {
        if (id !== other) assert.ok(Math.abs(p.x - q.x) >= 300 || Math.abs(p.y - q.y) >= 200);
    }
});
test('edge lanes and self loops produce distinct paths', () => {
    const a = {x: 300, y: 20}, b = {x: 400, y: 20};
    assert.notEqual(edgePath(a, b), edgePath(a, b, 1));
    assert.notEqual(edgePath(a, b), edgePath(a, a, 0, true));
});
