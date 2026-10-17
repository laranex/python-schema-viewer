import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {prepareDemo, demoDirectory} from '../scripts/prepare-demo.mjs';

test('synthetic demo is a laranex-schema document with links of both kinds', async () => {
    const schema = JSON.parse(await readFile(new URL('../demo/schema.json', import.meta.url), 'utf8'));
    assert.equal(schema.format, 'laranex-schema');
    const tables = new Map(schema.connections.flatMap(c => c.tables).map(t => [t.id, t]));
    assert.equal(tables.size, 9);
    for (const link of schema.links) for (const end of [link.from, link.to]) {
        assert.ok(end.columns.every(c => tables.get(end.table).columns.some(column => column.name === c)));
    }
    assert.ok(schema.links.some(l => l.state === 'both'));
    assert.ok(schema.links.some(l => l.state === 'foreign_key'));
});

test('demo uses the packaged viewer and relative, base-safe URLs', async () => {
    const {schema, manifest} = await prepareDemo();
    const html = await readFile(new URL('index.html', demoDirectory), 'utf8');
    assert.ok(html.includes('href="app.css"') && html.includes('src="app.js"'));
    assert.ok(html.includes('data-snapshot-url="snapshot.json"'));
    assert.ok(!html.match(/(?:src|href)="\/(?!\/)/));
    for (const {path} of manifest.files) await readFile(new URL(path, demoDirectory));
    assert.deepEqual(JSON.parse(await readFile(new URL('snapshot.json', demoDirectory))), schema);
});
