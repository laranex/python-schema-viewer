// Real Chromium smoke test via CDP, with no browser automation dependency.
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import assert from 'node:assert/strict';

const url = process.env.VIEWER_URL || 'http://127.0.0.1:8765';
const executable = process.env.CHROME_BIN || (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : 'google-chrome');
const profile = await mkdtemp(join(tmpdir(), 'laranex-browser-'));
const browser = spawn(executable, ['--headless', '--disable-gpu', '--no-first-run',
    '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
    {stdio: ['ignore', 'ignore', 'pipe']});
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
    await new Promise((resolve, reject) => {
        socket.onopen = resolve; socket.onerror = reject;
    });
    let counter = 0;
    const pending = new Map(), exceptions = [];
    socket.onmessage = event => {
        const data = JSON.parse(event.data);
        if (data.method === 'Runtime.exceptionThrown') exceptions.push(data.params.exceptionDetails);
        if (pending.has(data.id)) {
            const {resolve, reject, timer} = pending.get(data.id); pending.delete(data.id); clearTimeout(timer);
            if (data.error) reject(new Error(JSON.stringify(data.error))); else resolve(data.result);
        }
    };
    function send(method, params = {}, sessionId) {
        return new Promise((resolve, reject) => {
            const id = ++counter;
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timed out`)); }, 10000);
            pending.set(id, {resolve, reject, timer});
            socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
        });
    }
    const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
    const page = (method, params) => send(method, params, sessionId);
    await page('Runtime.enable'); await page('Page.enable');
    await page('Emulation.setDeviceMetricsOverride', {width: 1440, height: 950,
        deviceScaleFactor: 1, mobile: false});
    await page('Page.navigate', {url});
    async function evaluate(expression) {
        const result = await page('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    }
    for (let i = 0; i < 100; i++) {
        const loaded = await evaluate('document.body?.dataset.loaded');
        if (loaded === 'error') throw new Error(await evaluate('document.getElementById("status").textContent'));
        if (loaded === 'true') break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.equal(await evaluate('document.body.dataset.loaded'), 'true');
    const count = await evaluate('document.querySelectorAll(".table-card").length');
    assert.ok(count > 0);
    if (process.env.EXPECTED_TABLES) assert.equal(count, Number(process.env.EXPECTED_TABLES));
    const counts = await evaluate(`({
        fk: document.querySelectorAll('path[data-kind="foreign_key"]').length,
        orm: document.querySelectorAll('path[data-kind="orm"]').length
    })`);
    assert.ok(counts.fk > 0);
    await evaluate('document.getElementById("fk").click()');
    assert.equal(await evaluate('document.querySelectorAll(\'path[data-kind="foreign_key"]\').length'), 0);
    assert.equal(await evaluate('document.querySelectorAll(\'path[data-kind="orm"]\').length'), counts.orm);
    await evaluate('document.getElementById("fk").click(); document.getElementById("orm").click()');
    assert.equal(await evaluate('document.querySelectorAll(\'path[data-kind="foreign_key"]\').length'), counts.fk);
    assert.equal(await evaluate('document.querySelectorAll(\'path[data-kind="orm"]\').length'), 0);
    await evaluate('document.getElementById("orm").click()');
    await evaluate(`document.querySelector('#table-list button').click()`);
    assert.ok(await evaluate('document.querySelector("#details h2").textContent.length > 0'));
    // Exercise actual pointer capture and dragging through the browser input pipeline.
    const box = await evaluate(`(() => {const r=document.querySelector('.table-card.selected .table-title').getBoundingClientRect();return {x:r.x+30,y:r.y+15}})()`);
    const before = await evaluate('document.querySelector(".table-card.selected").style.left');
    await page('Input.dispatchMouseEvent', {type: 'mousePressed', ...box, button: 'left', clickCount: 1});
    await page('Input.dispatchMouseEvent', {type: 'mouseMoved', x: box.x + 60, y: box.y + 20, button: 'left', buttons: 1});
    await page('Input.dispatchMouseEvent', {type: 'mouseReleased', x: box.x + 60, y: box.y + 20, button: 'left', clickCount: 1});
    assert.notEqual(await evaluate('document.querySelector(".table-card.selected").style.left'), before);
    await evaluate('document.getElementById("fit").click()');
    const screenshot = await page('Page.captureScreenshot', {format: 'png'});
    await mkdir('.validation', {recursive: true});
    await writeFile('.validation/viewer.png', Buffer.from(screenshot.data, 'base64'));
    assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
    console.log(JSON.stringify({tables: count, ...counts, browser: 'passed', screenshot: '.validation/viewer.png'}));
} finally {
    socket?.close();
    if (browser.exitCode === null && browser.signalCode === null) {
        const exited = new Promise(resolve => browser.once('exit', resolve));
        browser.kill(); await exited;
    }
    await rm(profile, {recursive: true, force: true});
}

