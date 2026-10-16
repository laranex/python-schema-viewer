import {edgesFor, neighbors, matchesTable, layoutTables, edgePath} from './graph.js';

const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
};
const svg = (tag, attrs) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
};
const state = {doc: null, edges: [], visible: [], positions: new Map(), heights: new Map(),
    selected: null, edge: null, focus: null, x: 0, y: 0, scale: 1, drag: null};
const pretty = value => JSON.stringify(value, null, 2);
const label = table => `${table.schema_name ? table.schema_name + '.' : ''}${table.name}`;
const tableById = id => state.doc.tables.find(t => t.id === id);

function shownColumns(table) {
    if ($('columns').checked) return table.columns;
    const keys = new Set([...table.primary_key, ...table.foreign_keys.flatMap(f => f.columns)]);
    // Include referenced unique columns and ORM join columns too.
    for (const t of state.doc.tables) for (const fk of t.foreign_keys) {
        if (fk.target_table === table.id) fk.target_columns.forEach(c => keys.add(c));
    }
    for (const r of state.doc.relationships) for (const pair of r.join_pairs) {
        if (pair.local.table === table.id) keys.add(pair.local.column);
        if (pair.remote.table === table.id) keys.add(pair.remote.column);
    }
    return keys.size ? table.columns.filter(c => keys.has(c.name)) : table.columns.slice(0, 3);
}

function visibleEdges() {
    const ids = new Set(state.visible.map(t => t.id));
    return state.edges.filter(e => ids.has(e.source) && ids.has(e.target)
        && (e.kind === 'orm' ? $('orm').checked : $('fk').checked));
}

function transform() {
    $('world').style.transform = `translate(${state.x}px,${state.y}px) scale(${state.scale})`;
    $('zoom-label').textContent = `${Math.round(state.scale * 100)}%`;
}

function fit() {
    if (!state.visible.length) return;
    const boxes = state.visible.map(t => ({...state.positions.get(t.id), h: state.heights.get(t.id)}));
    const left = Math.min(...boxes.map(b => b.x)), top = Math.min(...boxes.map(b => b.y));
    const width = Math.max(...boxes.map(b => b.x + 380)) - left;
    const height = Math.max(...boxes.map(b => b.y + b.h)) - top;
    const view = $('canvas').getBoundingClientRect();
    state.scale = Math.max(0.035, Math.min(1.2, (view.width - 60) / width, (view.height - 100) / height));
    state.x = (view.width - width * state.scale) / 2 - left * state.scale;
    state.y = (view.height - height * state.scale) / 2 - top * state.scale;
    transform();
}

function zoom(factor, x = $('canvas').clientWidth / 2, y = $('canvas').clientHeight / 2) {
    const scale = Math.max(0.035, Math.min(2.5, state.scale * factor));
    state.x = x - (x - state.x) * scale / state.scale;
    state.y = y - (y - state.y) * scale / state.scale;
    state.scale = scale;
    transform();
}

function refresh(rearrange = false) {
    const scope = $('namespace').value;
    const nearby = state.focus ? neighbors(state.focus, state.edges) : null;
    state.visible = state.doc.tables.filter(t => (!scope || t.namespace === scope)
        && matchesTable(t, $('search').value, state.doc.models) && (!nearby || nearby.has(t.id)));
    for (const table of state.doc.tables) {
        const count = shownColumns(table).length;
        state.heights.set(table.id, 54 + count * 27 + (count < table.columns.length ? 30 : 0));
    }
    if (rearrange || !state.positions.size) {
        state.positions = layoutTables(state.doc.tables, state.edges, state.heights);
    }
    $('empty').hidden = state.visible.length > 0;
    $('unfocus').hidden = !state.focus;
    $('table-count').textContent = state.visible.length;
    $('table-list').replaceChildren();
    $('tables').replaceChildren();
    for (const table of state.visible) {
        const item = el('button', label(table), table.id === state.selected ? 'selected' : '');
        item.append(el('small', `${table.columns.length} columns · ${table.foreign_keys.length} foreign keys`));
        item.onclick = () => selectTable(table.id, true);
        $('table-list').append(item);
        const card = el('article', undefined, `table-card${table.id === state.selected ? ' selected' : ''}`);
        card.dataset.tableId = table.id;
        const position = state.positions.get(table.id);
        card.style.left = `${position.x}px`; card.style.top = `${position.y}px`;
        const header = el('div', undefined, 'table-title');
        const title = el('button', label(table)); title.append(el('small', table.namespace));
        title.onclick = () => selectTable(table.id);
        header.append(title, el('span', `${table.columns.length}`, 'tag'));
        header.onpointerdown = event => {
            if (event.button !== 0) return;
            event.stopPropagation();
            selectTable(table.id);
            state.drag = {id: table.id, x: event.clientX, y: event.clientY,
                startX: position.x, startY: position.y};
            $('canvas').setPointerCapture(event.pointerId);
        };
        card.append(header);
        for (const column of shownColumns(table)) {
            const row = el('div', undefined, 'column');
            row.dataset.column = column.name;
            const isFK = table.foreign_keys.some(f => f.columns.includes(column.name));
            row.append(el('span', column.primary_key ? 'PK' : isFK ? 'FK' : '', 'key'),
                el('span', column.name, 'column-name'), el('span', column.type, 'type'));
            row.title = `${column.name}: ${column.type}${column.nullable ? ' · nullable' : ' · required'}`;
            card.append(row);
        }
        const hidden = table.columns.length - shownColumns(table).length;
        if (hidden) card.append(el('div', `+ ${hidden} columns · enable All columns to expand`, 'table-more'));
        $('tables').append(card);
    }
    drawEdges();
}

function drawEdges() {
    const root = $('edges'); root.replaceChildren();
    const defs = svg('defs', {});
    for (const kind of ['foreign_key', 'orm']) {
        const marker = svg('marker', {id: `arrow-${kind}`, viewBox: '0 0 10 10', refX: 9,
            refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'});
        marker.append(svg('path', {d: 'M 0 0 L 10 5 L 0 10 z',
            fill: kind === 'orm' ? 'var(--accent)' : 'var(--fk)'}));
        defs.append(marker);
    }
    root.append(defs);
    const lanes = new Map();
    const endpoint = (id, column, side, offset) => {
        const position = state.positions.get(id);
        const index = shownColumns(tableById(id)).findIndex(c => c.name === column);
        return {x: position.x + (side === 'right' ? 300 : 0),
            y: position.y + (index >= 0 ? 54 + index * 27 + 13 : 28) + offset};
    };
    for (const edge of visibleEdges()) {
        const pair = [edge.source, edge.target].sort().join('|');
        const lane = lanes.get(pair) || 0; lanes.set(pair, lane + 1);
        const self = edge.source === edge.target;
        const offset = edge.kind === 'orm' ? 8 + lane * 4 : -4 - lane * 3;
        const start = endpoint(edge.source, edge.sourceColumn, 'right', offset);
        const end = endpoint(edge.target, edge.targetColumn, self ? 'right' : 'left', offset + (self ? 20 : 0));
        const d = edgePath(start, end, lane, self);
        const selected = edge.id === state.edge;
        const line = svg('path', {d, class: `edge ${edge.kind === 'orm' ? 'orm' : 'fk'}${selected ? ' selected' : ''}`,
            'data-kind': edge.kind, 'marker-end': `url(#arrow-${edge.kind})`});
        const title = svg('title', {}); title.textContent = edge.label; line.append(title);
        const hit = svg('path', {d, class: 'edge-hit', tabindex: '0', role: 'button',
            'aria-label': edge.label});
        const select = () => selectEdge(edge);
        hit.onclick = select;
        hit.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } };
        root.append(line, hit);
        if (edge.kind === 'orm' || selected) {
            const text = svg('text', {x: (start.x + end.x) / 2,
                y: (start.y + end.y) / 2 - 8 - lane * 13, class: 'edge-label', 'text-anchor': 'middle'});
            text.textContent = edge.label; root.append(text);
        }
    }
    const width = Math.max(800, ...state.visible.map(t => state.positions.get(t.id).x + 500));
    const height = Math.max(600, ...state.visible.map(t => state.positions.get(t.id).y + state.heights.get(t.id) + 100));
    root.setAttribute('width', width); root.setAttribute('height', height);
}

function collection(title, values) {
    const section = el('details');
    section.append(el('summary', `${title} (${values.length})`), el('pre', pretty(values)));
    return section;
}

function selectTable(id, center = false) {
    const table = tableById(id); state.selected = id; state.edge = null;
    const panel = $('details'); panel.replaceChildren(el('span', 'TABLE', 'tag'), el('h2', label(table)));
    const focus = el('button', 'Focus on neighbors');
    focus.onclick = () => { state.focus = id; $('search').value = ''; refresh(); fit(); };
    panel.append(focus);
    const dl = el('dl');
    for (const [name, value] of [['Namespace', table.namespace], ['Schema', table.schema_name || 'Unqualified'],
        ['Primary key', table.primary_key.join(', ') || 'None'], ['Comment', table.comment || '—']]) {
        dl.append(el('dt', name), el('dd', value));
    }
    panel.append(dl, collection('Columns', table.columns), collection('Indexes', table.indexes),
        collection('Constraints', table.constraints));
    const modelValues = state.doc.models.filter(m => m.table_ids.includes(id));
    panel.append(collection('Models', modelValues));
    for (const [kind, title] of [['foreign_key', 'Foreign keys'], ['orm', 'ORM relationships']]) {
        const links = state.edges.filter(e => e.kind === kind && (e.source === id || e.target === id));
        panel.append(el('h3', `${title} (${links.length})`));
        if (!links.length) panel.append(el('p', 'None declared', 'muted'));
        for (const edge of links) {
            const button = el('button', edge.label, 'detail-link');
            button.onclick = () => selectEdge(edge); panel.append(button);
        }
    }
    refresh();
    if (center) {
        const position = state.positions.get(id);
        state.scale = 0.9;
        state.x = $('canvas').clientWidth / 2 - (position.x + 150) * state.scale;
        state.y = 70 - position.y * state.scale; transform();
    }
}

function selectEdge(edge) {
    state.edge = edge.id; state.selected = null;
    const panel = $('details'); panel.replaceChildren(
        el('span', edge.kind === 'orm' ? 'DECLARED ORM RELATIONSHIP' : 'FOREIGN-KEY CONSTRAINT', 'tag'),
        el('h2', edge.label), el('pre', pretty(edge.detail)));
    for (const id of new Set([edge.source, edge.target])) {
        const table = tableById(id);
        if (table) {
            const button = el('button', label(table), 'detail-link');
            button.onclick = () => selectTable(id, true); panel.append(button);
        }
    }
    refresh();
}

$('canvas').onpointerdown = event => {
    if (event.button !== 0 || event.target.closest('.table-card,button,.edge-hit')) return;
    state.drag = {x: event.clientX, y: event.clientY, startX: state.x, startY: state.y};
    $('canvas').setPointerCapture(event.pointerId);
};
$('canvas').onpointermove = event => {
    const drag = state.drag; if (!drag) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (drag.id) {
        const position = state.positions.get(drag.id);
        position.x = drag.startX + dx / state.scale; position.y = drag.startY + dy / state.scale;
        const card = [...$('tables').children].find(c => c.dataset.tableId === drag.id);
        if (card) { card.style.left = `${position.x}px`; card.style.top = `${position.y}px`; }
        drawEdges();
    } else {
        state.x = drag.startX + dx; state.y = drag.startY + dy; transform();
    }
};
$('canvas').onpointerup = $('canvas').onpointercancel = () => { state.drag = null; };
$('canvas').addEventListener('wheel', event => {
    event.preventDefault(); const box = $('canvas').getBoundingClientRect();
    zoom(Math.exp(-event.deltaY * 0.0015), event.clientX - box.left, event.clientY - box.top);
}, {passive: false});
$('canvas').onkeydown = event => {
    if (event.target !== $('canvas')) return;
    if (event.key === '+' || event.key === '=') zoom(1.2);
    else if (event.key === '-') zoom(1 / 1.2);
    else if (event.key === '0') fit();
    else if (event.key.startsWith('Arrow')) {
        event.preventDefault();
        state.x += event.key === 'ArrowLeft' ? 50 : event.key === 'ArrowRight' ? -50 : 0;
        state.y += event.key === 'ArrowUp' ? 50 : event.key === 'ArrowDown' ? -50 : 0;
        transform();
    }
};
$('zoom-in').onclick = () => zoom(1.25); $('zoom-out').onclick = () => zoom(0.8);
$('fit').onclick = fit; $('arrange').onclick = () => { refresh(true); fit(); };
$('unfocus').onclick = () => { state.focus = null; refresh(); fit(); };
$('search').oninput = () => { refresh(); fit(); };
$('namespace').onchange = () => { state.focus = null; refresh(); fit(); };
for (const id of ['fk', 'orm']) $(id).onchange = () => drawEdges();
$('columns').onchange = () => { refresh(true); fit(); };
$('theme').onclick = () => {
    document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
};

try {
    const response = await fetch('/snapshot.json');
    if (!response.ok) throw new Error(`Snapshot request failed (${response.status})`);
    const doc = await response.json();
    if (doc.format !== 'laranex-schema-viewer' || doc.version !== 1) throw new Error('Unsupported snapshot format');
    state.doc = doc; state.edges = edgesFor(doc);
    for (const namespace of [...new Set(doc.tables.map(t => t.namespace))]) {
        const option = el('option', namespace); option.value = namespace; $('namespace').append(option);
    }
    const fk = doc.tables.reduce((sum, t) => sum + t.foreign_keys.length, 0);
    $('counts').textContent = `${doc.tables.length} tables · ${fk} foreign keys · ${doc.relationships.length} ORM relationships`;
    $('status').textContent = `Saved snapshot · ${doc.models.length} models · ${doc.diagnostics.length} diagnostics`;
    if (doc.diagnostics.length) $('details').append(collection('Build diagnostics', doc.diagnostics));
    refresh(true); requestAnimationFrame(fit);
    // Capture the initial state for deterministic browser smoke checks.
    document.body.dataset.loaded = 'true';
} catch (error) {
    $('status').textContent = `Could not load schema: ${error.message}`;
    $('details').replaceChildren(el('h2', 'Snapshot unavailable'), el('p', error.message));
    document.body.dataset.loaded = 'error';
}
