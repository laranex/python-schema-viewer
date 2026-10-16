import assert from 'node:assert/strict';
import {readFile, readdir, stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {schema} from '../demo/schema.mjs';

const dist = path.resolve(fileURLToPath(new URL('../.vitepress/dist/', import.meta.url)));
const docs = fileURLToPath(new URL('../../docs/', import.meta.url));
const base = '/python-schema-viewer/';
const excluded = new Set(['architecture', 'validation', 'agent-skills-validation']);
const output = new Set(await readdir(dist));
for (const name of await readdir(docs)) {
    if (!name.endsWith('.md') || name === 'README.md') continue;
    const page = name.replace(/\.md$/, '');
    assert.equal(output.has(`${page}.html`), !excluded.has(page), `Publication boundary: ${name}`);
}

let checked = 0;
for (const filename of output) {
    if (!filename.endsWith('.html')) continue;
    const html = await readFile(path.join(dist, filename), 'utf8');
    for (const [, value] of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
        const url = new URL(value.replaceAll('&amp;', '&'), `https://example.invalid${base}${filename}`);
        if (url.origin !== 'https://example.invalid') continue;
        assert.ok(url.pathname.startsWith(base), `Wrong base in ${filename}: ${value}`);
        const relative = decodeURIComponent(url.pathname.slice(base.length));
        let target = path.resolve(dist, relative);
        assert.ok(target.startsWith(dist + path.sep) || target === dist);
        if ((await stat(target)).isDirectory()) target = path.join(target, 'index.html');
        assert.ok((await stat(target)).isFile(), `Missing asset/page: ${value}`);
        checked++;
    }
}
const intro = await readFile(path.join(dist, 'introduction.html'), 'utf8');
assert.ok(intro.includes('/edit/main/docs/introduction.md'), 'Edit link must point at docs/');
assert.ok(intro.includes(`${base}demo/index.html`), 'Embedded demo must retain the Pages base');
const quickStart = await readFile(path.join(dist, 'quick-start.html'), 'utf8');
assert.ok(quickStart.includes('language-python') && quickStart.includes('shiki'), 'Code highlighting missing');
assert.deepEqual(JSON.parse(await readFile(path.join(dist, 'demo/snapshot.json'), 'utf8')), schema);
console.log(`Checked ${checked} production links/assets, documentation boundaries, and synthetic demo.`);
