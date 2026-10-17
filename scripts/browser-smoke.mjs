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
        // Chrome requests /favicon.ico by itself; the viewer page declares no icon, so its 404 is not a page error.
        if (data.method === 'Log.entryAdded' && params.entry.level === 'error'
            && !(params.entry.source === 'network' && params.entry.url === new URL('/favicon.ico', url).href)) {
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
    async function open(width, height, mobile) {
        await page('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile});
        await page('Page.navigate', {url: url.href});
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
    const nodes = await until(`(() => { const ids = [...document.querySelectorAll('.react-flow__node[data-id]')]
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
    await until(`(row => row && (row.click(), true))(document.querySelector('aside.sheet.open button.browse__row'))`,
        'a browse row');
    await until(`document.querySelector('aside.sheet.open .details__title')`, 'table details in the sheet');
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
