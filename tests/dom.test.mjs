// Actual app + HTML in a DOM implementation. This does not test browser layout.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

const {parseHTML} = await import(process.env.DOM_MODULE || 'linkedom');
const html = await readFile(new URL('../src/laranex_schema_viewer/static/index.html', import.meta.url), 'utf8');
const {document} = parseHTML(html);
globalThis.document = document;
globalThis.requestAnimationFrame = callback => callback();
const $ = id => document.getElementById(id);
$('canvas').getBoundingClientRect = () => ({width: 900, height: 600, left: 0, top: 0});
Object.defineProperties($('canvas'), {clientWidth: {value: 900}, clientHeight: {value: 600}});
$('canvas').setPointerCapture = () => {};
for (const input of document.querySelectorAll('input[type=checkbox]')) input.checked = input.hasAttribute('checked');
const column = (name, pk = false) => ({name, type: 'INTEGER', primary_key: pk, nullable: false});
const base = {namespace: 'default', schema_name: null, comment: null, primary_key: ['id'],
    indexes: [], constraints: []};
const fixture = {
    format: 'laranex-schema-viewer', version: 1,
    tables: [
        {...base, id: 'a', name: 'users', columns: [column('id', true), column('team_id'), column('email')],
            foreign_keys: [{id: 'fk1', columns: ['team_id'], target_table: 'b', target_columns: ['id']}]},
        {...base, id: 'b', name: '<img src=x onerror=alert(1)>', columns: [column('id', true)], foreign_keys: []},
    ],
    models: [{id: 'm1', class_name: 'example.User', table_ids: ['a']}],
    relationships: [{id: 'orm1', name: 'team', direction: 'MANYTOONE', source_table: 'a',
        target_table: 'b', join_pairs: [], secondary: null}], diagnostics: [],
};
const doc = process.env.SNAPSHOT ? JSON.parse(await readFile(process.env.SNAPSHOT, 'utf8')) : fixture;
globalThis.fetch = async path => {
    assert.equal(path, '/snapshot.json');
    return {ok: true, json: async () => doc};
};

test('viewer renders a saved snapshot and supports interactions', async t => {
    await import('../src/laranex_schema_viewer/static/app.js');
    assert.equal(document.body.dataset.loaded, 'true');
    assert.equal(document.querySelectorAll('.table-card').length, doc.tables.length);
    assert.equal(document.querySelectorAll('img').length, 0);
    const fkCount = doc.tables.reduce((n, table) => n + table.foreign_keys.length, 0);
    assert.equal(document.querySelectorAll('path[data-kind="foreign_key"]').length, fkCount);
    if (process.env.SNAPSHOT) {
        await t.test('reference search, index details, focus, zoom and column expansion', () => {
            const table = doc.tables.find(t => t.name === 'users') || doc.tables[0];
            const name = `${table.schema_name ? table.schema_name + '.' : ''}${table.name}`;
            $('search').value = table.name; $('search').oninput();
            const button = [...$('table-list').children].find(b => b.textContent.startsWith(name));
            assert.ok(button, 'reference table must be found by search');
            button.onclick();
            assert.ok($('details').textContent.includes('Primary key'));
            for (const index of table.indexes) {
                if (index.name) assert.ok($('details').textContent.includes(index.name));
                for (const predicate of Object.values(index.predicates)) {
                    assert.ok($('details').textContent.includes(JSON.stringify(predicate)));
                }
            }
            $('details').querySelector('button').onclick();
            assert.equal($('unfocus').hidden, false);
            assert.ok(document.querySelectorAll('.table-card').length > 0);
            const before = $('world').style.transform;
            $('zoom-in').onclick();
            assert.notEqual($('world').style.transform, before);
            $('unfocus').onclick();
            assert.equal(document.querySelectorAll('.table-card').length, doc.tables.length);
            $('columns').checked = true; $('columns').onchange();
            assert.equal(document.querySelectorAll('.column').length,
                doc.tables.reduce((n, table) => n + table.columns.length, 0));
            assert.equal(document.querySelectorAll('path[data-kind="foreign_key"]').length, fkCount);
            $('fk').checked = false; $('fk').onchange();
            assert.equal(document.querySelectorAll('path[data-kind="foreign_key"]').length, 0);
            $('fk').checked = true; $('fk').onchange();
            document.querySelector('.edge-hit').onclick();
            assert.ok($('details').textContent.includes('FOREIGN-KEY CONSTRAINT'));
        });
        return;
    }

    await t.test('foreign-key and ORM layers are independent', () => {
        assert.equal(document.querySelectorAll('path[data-kind="orm"]').length, 1);
        $('fk').checked = false; $('fk').onchange();
        assert.equal(document.querySelectorAll('path[data-kind="foreign_key"]').length, 0);
        assert.equal(document.querySelectorAll('path[data-kind="orm"]').length, 1);
        $('fk').checked = true; $('fk').onchange();
        $('orm').checked = false; $('orm').onchange();
        assert.equal(document.querySelectorAll('path[data-kind="foreign_key"]').length, 1);
        assert.equal(document.querySelectorAll('path[data-kind="orm"]').length, 0);
        $('orm').checked = true; $('orm').onchange();
    });
    await t.test('search, selection, focus and details', () => {
        $('search').value = 'email'; $('search').oninput();
        assert.equal(document.querySelectorAll('.table-card').length, 1);
        $('table-list').firstElementChild.onclick();
        assert.ok($('details').textContent.includes('Primary key'));
        $('details').querySelector('button').onclick();
        assert.equal(document.querySelectorAll('.table-card').length, 2);
        assert.equal($('unfocus').hidden, false);
        $('unfocus').onclick();
        assert.equal($('unfocus').hidden, true);
    });
    await t.test('edge selection identifies its own definition', () => {
        document.querySelector('.edge-hit').onclick();
        assert.ok($('details').textContent.includes('FOREIGN-KEY CONSTRAINT'));
    });
    await t.test('column expansion, zoom, drag and theme', () => {
        const before = document.querySelectorAll('.column').length;
        $('columns').checked = true; $('columns').onchange();
        assert.ok(document.querySelectorAll('.column').length > before);
        const transform = $('world').style.transform;
        $('zoom-in').onclick();
        assert.notEqual($('world').style.transform, transform);
        document.querySelector('.table-title').onpointerdown({button: 0, clientX: 100,
            clientY: 100, pointerId: 1, stopPropagation() {}});
        const first = document.querySelector('.table-card');
        const left = first.style.left;
        $('canvas').onpointermove({clientX: 150, clientY: 100});
        assert.notEqual(first.style.left, left);
        $('canvas').onpointerup();
        $('theme').onclick();
        assert.equal(document.documentElement.dataset.theme, 'dark');
    });
});
