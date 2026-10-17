// Real-browser check of the served shared viewer over the Chrome DevTools Protocol, with no automation dependency.
//
//   VIEWER_URL=http://127.0.0.1:8765/ EXPECT='{"source":"combined",...}' node scripts/browser-smoke.mjs
//
// EXPECT (JSON, every key optional except source):
//   source         document source the Details overview must show (declared | database | combined)
//   tables         number of table nodes (every table in /snapshot.json must render as a node regardless)
//   connections    number of connection coverage blocks
//   coverage       coverage kinds shown per connection, e.g. ["database", "declared"]
//   diagnostics    exact Diagnostics tab count; the tab must list as many cards
//   table          {name, indexes: [...], relationships: [...], drift: [...columns], provenance: {column: badge text}}
//   multiConnection {regions: {connection name: driver}, duplicate: table name on several connections,
//                  drift: {connection name: [...drifted columns of the duplicate there]}}: frames, connection-qualified
//                  labels, the connection select and ] key, reference nodes and cross-connection (ORM-only) edges for
//                  every link between connections in /snapshot.json, #connection= and #table= deep links, search, per-
//                  connection diagnostics, drift and coverage, and the duplicate's connection at phone width
// Fails on any uncaught exception, console error, CSP violation or request outside the viewer's origin.
// SCREENSHOT=path writes a desktop screenshot.
import {spawn} from 'node:child_process';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';

const url = new URL(process.env.VIEWER_URL || 'http://127.0.0.1:8765/');
const expect = JSON.parse(process.env.EXPECT || '{}');
const executable = process.env.CHROME_BIN || (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : 'google-chrome');
const snapshot = await (await fetch(new URL('snapshot.json', url))).json();
const tableIds = snapshot.connections.flatMap(c => c.tables.map(t => t.id)).sort();
const connectionOf = new Map(snapshot.connections.flatMap(c => c.tables.map(t => [t.id, c])));

const profile = await mkdtemp(join(tmpdir(), 'laranex-browser-'));
const browser = spawn(executable, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
let socket;
try {
    const endpoint = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Chrome startup timed out')), 15000);
        let output = '';
        browser.stderr.on('data', data => {
            output += data;
            const found = output.match(/DevTools listening on (ws:\/\/\S+)/);
            if (found) { clearTimeout(timer); resolve(found[1]); }
        });
        browser.on('error', error => { clearTimeout(timer); reject(error); });
        browser.on('exit', (code, signal) => {
            clearTimeout(timer); reject(new Error(`Chrome exited: ${code ?? signal}; ${output.slice(-1000)}`));
        });
    });
    socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });

    let counter = 0;
    const pending = new Map(), problems = [], requests = [];
    socket.onmessage = event => {
        const data = JSON.parse(event.data);
        const params = data.params;
        if (data.method === 'Runtime.exceptionThrown') problems.push(`exception: ${JSON.stringify(params.exceptionDetails)}`);
        if (data.method === 'Runtime.consoleAPICalled' && (params.type === 'error' || params.type === 'assert')) {
            problems.push(`console.${params.type}: ${params.args.map(a => a.value ?? a.description).join(' ')}`);
        }
        if (data.method === 'Log.entryAdded' && params.entry.level === 'error') {
            problems.push(`log (${params.entry.source}): ${params.entry.text} ${params.entry.url ?? ''}`);
        }
        if (data.method === 'Network.requestWillBeSent') requests.push(params.request.url);
        if (pending.has(data.id)) {
            const {resolve, reject, timer} = pending.get(data.id); pending.delete(data.id); clearTimeout(timer);
            if (data.error) reject(new Error(JSON.stringify(data.error))); else resolve(data.result);
        }
    };
    function send(method, params = {}, sessionId) {
        return new Promise((resolve, reject) => {
            const id = ++counter;
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out`)); }, 15000);
            pending.set(id, {resolve, reject, timer});
            socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
        });
    }
    const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
    const page = (method, params) => send(method, params, sessionId);
    await page('Runtime.enable'); await page('Page.enable'); await page('Log.enable'); await page('Network.enable');
    await page('Page.addScriptToEvaluateOnNewDocument', {source: `window.__csp = [];
        document.addEventListener('securitypolicyviolation',
            e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI), true);`});

    async function evaluate(expression) {
        const result = await page('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
        if (result.exceptionDetails) throw new Error(`${expression}: ${JSON.stringify(result.exceptionDetails)}`);
        return result.result.value;
    }
    async function until(expression, what, timeout = 15000) {
        const end = Date.now() + timeout;
        let value;
        while (Date.now() < end) {
            value = await evaluate(`(v => v instanceof Node || v instanceof Function ? true : v)(${expression})`);
            if (value) return value;
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error(`Timed out waiting for ${what}: ${expression} -> ${JSON.stringify(value)}`);
    }
    async function open(width, height, mobile, hash = '') {
        await page('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile});
        // Through about:blank, so a URL that differs only in its hash still loads the viewer afresh.
        await page('Page.navigate', {url: 'about:blank'});
        await page('Page.navigate', {url: url.href + hash});
        await until(`document.querySelector('.lsv')?.dataset.loaded === 'true'
            || (document.querySelector('.lsv')?.dataset.loaded === 'error' && document.body.innerText)`, 'the viewer');
        assert.equal(await evaluate(`document.querySelector('.lsv').dataset.loaded`), 'true',
            await evaluate('document.body.innerText'));
    }
    const tab = name => `[...document.querySelectorAll('aside[aria-label="Side panel"] [role=tab]')]
        .find(t => t.textContent.trim().startsWith(${JSON.stringify(name)}))`;
    const panel = `document.querySelector('aside[aria-label="Side panel"]')`;
    const result = {source: expect.source};

    // Desktop: the diagram.
    await open(1440, 950, false);
    const nodes = await until(`(() => { const ids = [...document.querySelectorAll('.react-flow__node-table[data-id]')]
        .map(n => n.dataset.id); return ids.length >= ${tableIds.length} && ids; })()`, 'every table node');
    assert.deepEqual([...nodes].sort(), tableIds, 'every table renders as one node');
    if (expect.tables !== undefined) assert.equal(nodes.length, expect.tables);
    result.tables = nodes.length;
    result.edges = await until(`document.querySelectorAll('.react-flow__edge').length`, 'edges');

    // Details overview: source badge and coverage.
    await evaluate(`${tab('Details')}.click()`);
    await until(`${panel}.querySelector('.source-mode .badge')`, 'the source badge');
    assert.equal(await evaluate(`${panel}.querySelector('.source-mode .badge').textContent.trim()`), expect.source);
    const coverage = await evaluate(`[...${panel}.querySelectorAll('.connection-coverage')]
        .map(c => [...c.querySelectorAll('.coverage')].map(b => b.querySelector('.badge').textContent.trim()))`);
    if (expect.connections !== undefined) assert.equal(coverage.length, expect.connections, 'coverage per connection');
    if (expect.coverage) for (const kinds of coverage) assert.deepEqual(kinds, expect.coverage);
    result.coverage = coverage;

    // Diagnostics tab: its count and its cards.
    const label = await evaluate(`${tab('Diagnostics')}.textContent.trim()`);
    const count = Number(label.replace('Diagnostics', '') || 0);
    if (expect.diagnostics !== undefined) assert.equal(count, expect.diagnostics, `diagnostics tab "${label}"`);
    await evaluate(`${tab('Diagnostics')}.click()`);
    if (count > 0) {
        assert.equal(await until(`${panel}.querySelectorAll('.cards > li').length`, 'diagnostic cards'), count);
        result.certainty = await evaluate(`[...new Set([...${panel}.querySelectorAll('.card__eyebrow')]
            .map(e => e.textContent.split('·')[1].trim()))]`);
    }
    result.diagnostics = count;

    // A table's details, opened from Browse.
    if (expect.table) {
        const t = expect.table;
        await evaluate(`${tab('Browse')}.click()`);
        await until(`(row => row && (row.click(), true))([...document.querySelectorAll('button.browse__row')].find(b =>
            b.querySelector('.browse__title').firstChild.textContent.trim() === ${JSON.stringify(t.name)}))`,
        `the ${t.name} browse row`);
        await until(`${panel}.querySelector('.details__title')?.textContent.includes(${JSON.stringify(t.name)})`,
            `the ${t.name} details`);
        const details = await evaluate(`(() => {
            const p = ${panel};
            const section = title => [...p.querySelectorAll('details')].find(d =>
                d.querySelector('summary').firstChild.textContent.trim() === title);
            const rows = [...p.querySelectorAll('.columns-table tr')].map(r => ({
                name: r.querySelector('th').firstChild.textContent.trim(), drift: r.classList.contains('drift'),
                driftList: !!r.querySelector('.drift-list'),
                badges: [...r.querySelectorAll('td > .badge')].map(b => b.textContent.trim())}));
            return {rows, indexes: [...p.querySelectorAll('li.index .index-head strong')].map(s => s.textContent),
                relationships: [...(section('ORM relationships')?.querySelectorAll('ul.plain > li > strong') ?? [])]
                    .map(s => s.textContent),
                foreignKeys: [...(section('Foreign keys')?.querySelectorAll('ul.plain > li') ?? [])].length};
        })()`);
        for (const name of t.indexes ?? []) assert.ok(details.indexes.includes(name), `index ${name} in ${details.indexes}`);
        for (const name of t.relationships ?? []) {
            assert.ok(details.relationships.includes(name), `relationship ${name} in ${details.relationships}`);
        }
        const row = name => details.rows.find(r => r.name === name) ?? assert.fail(`column ${name} not listed`);
        for (const name of t.drift ?? []) assert.ok(row(name).drift && row(name).driftList, `drift on ${name}`);
        for (const [name, badge] of Object.entries(t.provenance ?? {})) {
            assert.ok(row(name).badges.includes(badge), `${name} badges ${row(name).badges}`);
        }
        if (expect.source !== 'combined') {
            assert.ok(details.rows.every(r => !r.drift && !r.badges.some(b => b.endsWith(' only'))),
                'drift and provenance badges appear only for combined documents');
        }
        result.table = details;
    }

    // Several connections: frames, qualified labels, one-connection views with reference nodes, deep links, search,
    // and diagnostics, drift and coverage that stay on their own connection.
    const multi = expect.multiConnection;
    const q = JSON.stringify;
    const node = id => `[...document.querySelectorAll('.react-flow__node')].find(n => n.dataset.id === ${q(id)})`;
    const select = `document.querySelector('select[aria-label="Connection"]')`;
    const hashOf = (key, value) => `#${key}=${encodeURIComponent(value)}`;
    const duplicates = multi && Object.fromEntries(snapshot.connections.flatMap(c => c.tables
        .filter(t => t.name === multi.duplicate).map(t => [c.name, t.id])));
    async function view() {
        return evaluate(`({
            connection: ${select}.value,
            regions: [...document.querySelectorAll('.region-node')].map(r => [
                r.querySelector('.region-node__name').textContent.trim(), r.querySelector('.region-node__meta').textContent.trim()]),
            tables: [...document.querySelectorAll('.react-flow__node-table[data-id]')].map(n => n.dataset.id).sort(),
            externals: [...document.querySelectorAll('.react-flow__node-external')].map(n => [n.dataset.id,
                n.querySelector('.external-node__label').textContent, n.querySelector('.external-node__name').textContent]),
            cross: [...document.querySelectorAll('.react-flow__edge-path.cross-connection')].map(p => [...p.classList]),
            hash: decodeURIComponent(location.hash)})`);
    }
    // The links between connections a view must draw: those with an end on a table it shows.
    function crossEdges(state, links) {
        const shown = new Set(state.tables);
        const expected = links.filter(l => shown.has(l.from.table) || shown.has(l.to.table));
        assert.equal(state.cross.length, expected.length, `cross-connection edges ${q(state.cross)}`);
        for (const classes of state.cross) {
            assert.ok(classes.includes('edge--relationship'), `cross-connection edge is ORM-only: ${classes}`);
            assert.ok(!classes.includes('edge--foreign_key') && !classes.includes('edge--both'), `no FK styling: ${classes}`);
        }
        return expected;
    }
    async function details() {
        await until(`${panel}.querySelector('.details__title')`, 'table details');
        return evaluate(`({title: ${panel}.querySelector('.details__title').textContent.trim(),
            subtitle: ${panel}.querySelector('.details__subtitle').textContent.trim(),
            drift: [...${panel}.querySelectorAll('.columns-table tr.drift th')].map(t => t.firstChild.textContent.trim())})`);
    }
    const labelOf = (name, table) => `${name} · ${table}`;
    if (multi) {
        const names = Object.keys(multi.regions);
        const cross = snapshot.links.filter(l => connectionOf.get(l.from.table) !== connectionOf.get(l.to.table));
        assert.ok(cross.length > 0, 'the document links two connections');
        assert.ok(cross.every(l => l.foreignKey === null), 'no foreign key crosses connections');
        assert.deepEqual(Object.keys(duplicates).sort(), [...names].sort(), `${multi.duplicate} on every connection`);
        const report = {};

        // All connections: one frame per connection, both duplicates as distinct, qualified cards.
        await open(1440, 950, false);
        await until(`document.querySelectorAll('.region-node').length === ${names.length}`, 'a frame per connection');
        let state = await view();
        assert.equal(state.connection, '', 'all connections by default');
        assert.deepEqual(state.regions.map(([name]) => name).sort(), [...names].sort(), 'frame names');
        for (const [name, meta] of state.regions) {
            assert.ok(meta.startsWith(`${multi.regions[name]} · `), `frame ${name} shows its driver: ${meta}`);
        }
        for (const [name, id] of Object.entries(duplicates)) {
            assert.equal(await evaluate(`${node(id)}.querySelector('.table-title button').title`),
                labelOf(name, multi.duplicate));
            assert.equal(await evaluate(`${node(id)}.querySelector('.table-connection').textContent.trim()`), `${name} ·`);
        }
        crossEdges(state, cross);
        report.regions = state.regions;

        // One connection, from the select: the other end of each cross-connection link is a reference node.
        const link = cross[0];
        const home = connectionOf.get(link.to.table), away = connectionOf.get(link.from.table);
        await evaluate(`(s => (s.value = ${q(home.id)}, s.dispatchEvent(new Event('change', {bubbles: true}))))(${select})`);
        await until(`document.querySelectorAll('.region-node').length === 0 && ${node(link.from.table)}`, 'the reference node');
        state = await view();
        assert.equal(state.connection, home.id);
        assert.ok(state.hash.includes(`connection=${home.id}`), `hash ${state.hash}`);
        assert.deepEqual(state.tables, home.tables.map(t => t.id).sort(), `only ${home.name} tables as cards`);
        const reference = state.externals.find(([id]) => id === link.from.table);
        assert.ok(reference && reference[1] === `On connection ${away.name} · ${away.driver}`
            && reference[2] === away.tables.find(t => t.id === link.from.table).name, `reference ${q(state.externals)}`);
        crossEdges(state, cross);
        report.reference = reference.slice(1);

        // Clicking the reference opens its table on its own connection.
        await evaluate(`${node(link.from.table)}.querySelector('button').click()`);
        await until(`${select}.value === ${q(away.id)}`, 'the reference to open its connection');
        const opened = await details();
        assert.ok(opened.title.startsWith(labelOf(away.name, connectionOf.get(link.from.table).tables
            .find(t => t.id === link.from.table).name)), `opened ${opened.title}`);
        await until(`${node(link.to.table)}?.classList.contains('react-flow__node-external')`, 'the way back as a reference');

        // ] steps to the next connection (all connections come first, then each in document order).
        const order = ['', ...snapshot.connections.map(c => c.id)];
        const next = order[(order.indexOf(away.id) + 1) % order.length];
        await evaluate(`document.body.dispatchEvent(new KeyboardEvent('keydown', {key: ']', bubbles: true}))`);
        await until(`${select}.value === ${q(next)}`, 'the ] key to step connections');
        report.step = [away.id, next];

        // Deep links.
        for (const c of snapshot.connections) {
            await open(1440, 950, false, hashOf('connection', c.id));
            await until(`${node(c.tables[0].id)}`, `#connection=${c.id}`);
            state = await view();
            assert.equal(state.connection, c.id);
            assert.deepEqual(state.tables, c.tables.map(t => t.id).sort());
            assert.equal(state.regions.length, 0);
        }
        for (const [name, id] of Object.entries(duplicates)) {
            await open(1440, 950, false, hashOf('table', id));
            const shown = await details();
            assert.ok(shown.title.startsWith(labelOf(name, multi.duplicate)), `#table=${id} opened ${shown.title}`);
            assert.ok(shown.subtitle.startsWith(name), `subtitle ${shown.subtitle}`);
            assert.deepEqual(shown.drift, multi.drift?.[name] ?? [], `drift on ${name} ${multi.duplicate}`);
        }
        await open(1440, 950, false, hashOf('connection', 'connection:no-such-connection'));
        await until(`document.querySelectorAll('.region-node').length === ${names.length}`, 'every connection');
        assert.equal((await view()).connection, '', 'an unknown connection opens every connection');

        // Search finds every duplicate, each labelled and described with its connection.
        await evaluate(`(i => { i.focus(); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
            .call(i, ${q(multi.duplicate)}); i.dispatchEvent(new Event('input', {bubbles: true})); })(
            document.querySelector('input[aria-label="Search the schema"]'))`);
        await until(`document.querySelector('.search__result')`, 'search results');
        const found = await evaluate(`[...document.querySelectorAll('.search__result')]
            .filter(r => r.querySelector('.search__kind').textContent === 'Table')
            .map(r => [r.querySelector('.search__label').textContent, r.querySelector('small').textContent])`);
        for (const name of names) {
            const hit = found.find(([label]) => label === labelOf(name, multi.duplicate));
            assert.ok(hit && hit[1].startsWith(`${name} (${multi.regions[name]})`), `search ${q(found)}`);
        }
        report.search = found;

        // Diagnostics name only the drifted connections; coverage is listed per connection.
        const drifted = Object.keys(multi.drift ?? {});
        await evaluate(`${tab('Diagnostics')}.click()`);
        const cards = await evaluate(`[...${panel}.querySelectorAll('.cards > li')].map(c => c.innerText)`);
        for (const card of cards) {
            assert.ok(drifted.some(name => card.includes(`${name} · `) || card.includes(`Connection ${name}:`)), card);
            for (const name of names.filter(n => !drifted.includes(n))) {
                assert.ok(!card.includes(`${name} · `) && !card.includes(`Connection ${name}:`), `${name} in ${card}`);
            }
        }
        await evaluate(`${tab('Details')}.click()`);
        await until(`${panel}.querySelector('.connection-coverage')`, 'coverage');
        const coverage = await evaluate(`[...${panel}.querySelectorAll('.connection-coverage')].map(c =>
            [c.querySelector('strong').textContent, c.querySelector('.muted').textContent])`);
        assert.deepEqual(coverage, snapshot.connections.map(c => [c.name, c.driver]), 'coverage per connection');
        report.diagnostics = cards.length;
        result.multiConnection = report;
    }

    // Theme toggle, remembered across a reload.
    const before = await evaluate(`document.querySelector('.lsv').dataset.theme`);
    await evaluate(`document.querySelector('button[aria-label^="Switch to"]').click()`);
    const after = await until(`document.querySelector('.lsv').dataset.theme !== ${JSON.stringify(before)}
        && document.querySelector('.lsv').dataset.theme`, 'the theme to change');
    if (process.env.SCREENSHOT) {
        const shot = await page('Page.captureScreenshot', {format: 'png'});
        await writeFile(process.env.SCREENSHOT, Buffer.from(shot.data, 'base64'));
    }
    await open(1440, 950, false);
    assert.equal(await evaluate(`document.querySelector('.lsv').dataset.theme`), after, 'theme remembered');
    result.theme = [before, after];

    // Phone width: no horizontal scroll, and the side panel opens as a sheet.
    await open(390, 844, true);
    assert.ok(await evaluate(`document.querySelector('.lsv').classList.contains('narrow')`), 'phone layout');
    assert.ok(await evaluate(`document.documentElement.scrollWidth <= document.documentElement.clientWidth`),
        'no horizontal scroll');
    await evaluate(`document.querySelector('.sheet-bar__action').click()`);
    await until(`document.querySelector('aside.sheet.open [role=tab]')`, 'the side panel sheet');
    if (multi) {
        // A duplicate-named table's details in the sheet name its connection.
        const [name] = Object.keys(duplicates).sort();
        await until(`(row => row && (row.click(), true))([...document.querySelectorAll('aside.sheet.open button.browse__row')]
            .find(b => b.querySelector('.browse__title').firstChild.textContent.trim() === ${q(labelOf(name, multi.duplicate))}))`,
        `the ${name} ${multi.duplicate} browse row`);
        await until(`document.querySelector('aside.sheet.open .details__title')?.textContent.startsWith(
            ${q(labelOf(name, multi.duplicate))}) && document.querySelector('aside.sheet.open .details__subtitle')
            .textContent.startsWith(${q(name)})`, 'the duplicate and its connection in the sheet');
    } else {
        await until(`(row => row && (row.click(), true))(document.querySelector('aside.sheet.open button.browse__row'))`,
            'a browse row');
        await until(`document.querySelector('aside.sheet.open .details__title')`, 'table details in the sheet');
    }
    result.phone = 'sheet';

    const csp = await evaluate('window.__csp');
    assert.deepEqual(csp, [], 'CSP violations');
    assert.deepEqual(problems, [], 'exceptions or console errors');
    const foreign = requests.filter(r => !r.startsWith(url.origin + '/') && !/^(data|blob):/.test(r));
    assert.deepEqual(foreign, [], 'requests outside the local server');
    result.requests = requests.length;
    console.log(JSON.stringify(result));
} finally {
    socket?.close();
    if (browser.exitCode === null && browser.signalCode === null) {
        const exited = new Promise(resolve => browser.once('exit', resolve));
        browser.kill(); await exited;
    }
    // Chrome helper processes can still write to the profile after the main process exits.
    await rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 100});
}
