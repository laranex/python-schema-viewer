import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {schema} from '../demo/schema.mjs';
import {prepareDemo, demoDirectory} from '../scripts/prepare-demo.mjs';
import {edgesFor} from '../../src/laranex_schema_viewer/static/graph.js';

test('synthetic graph has valid endpoints and independent FK/ORM paths', () => {
    const tables = new Map(schema.tables.map(t => [t.id, t]));
    const models = new Set(schema.models.map(m => m.id));
    for (const table of tables.values()) for (const fk of table.foreign_keys) {
        assert.ok(tables.has(fk.target_table));
        assert.equal(fk.columns.length, fk.target_columns.length);
        for (const column of fk.target_columns) assert.ok(tables.get(fk.target_table).columns.some(c => c.name === column));
    }
    for (const relation of schema.relationships) {
        assert.ok(models.has(relation.model) && models.has(relation.target_model));
        for (const pair of relation.join_pairs) for (const endpoint of [pair.local, pair.remote]) {
            assert.ok(tables.get(endpoint.table).columns.some(c => c.name === endpoint.column));
        }
    }
    const edges = edgesFor(schema);
    assert.ok(edges.some(e => e.kind === 'foreign_key'));
    assert.ok(edges.some(e => e.kind === 'orm'));
    assert.ok(edges.some(e => e.source === e.target));
    assert.equal(new Set(edges.map(e => e.id)).size, edges.length);
    assert.equal(schema.tables.length, 9);
});

test('demo uses packaged viewer assets and relative, base-safe URLs', async () => {
    await prepareDemo();
    const html = await readFile(new URL('index.html', demoDirectory), 'utf8');
    const app = await readFile(new URL('app.js', demoDirectory), 'utf8');
    assert.ok(html.includes('href="./styles.css"'));
    assert.ok(html.includes('src="./app.js"'));
    assert.ok(app.includes("fetch('./snapshot.json')"));
    assert.ok(!html.match(/(?:src|href)="\/(?!\/)/));
    assert.deepEqual(JSON.parse(await readFile(new URL('snapshot.json', demoDirectory))), schema);
    const source = await readFile(new URL('../scripts/prepare-demo.mjs', import.meta.url), 'utf8');
    assert.ok(!source.includes('reference.json'));
});
