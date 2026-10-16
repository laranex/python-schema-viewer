// Pure snapshot-to-graph functions. Foreign keys and ORM declarations stay separate.
export function edgesFor(doc) {
    const edges = [];
    for (const table of doc.tables) for (const fk of table.foreign_keys) {
        edges.push({id: fk.id, kind: 'foreign_key', source: table.id,
            target: fk.target_table, sourceColumn: fk.columns[0], targetColumn: fk.target_columns[0],
            label: `FK · ${fk.columns.join(', ')} → ${fk.target_columns.join(', ')}`, detail: fk});
    }
    for (const relation of doc.relationships) {
        const base = {kind: 'orm', label: `${relation.name} · ${relation.direction.toLowerCase()}`,
            detail: relation};
        if (relation.secondary) {
            edges.push({...base, id: `${relation.id}:source`, source: relation.source_table,
                target: relation.secondary});
            edges.push({...base, id: `${relation.id}:target`, source: relation.secondary,
                target: relation.target_table});
        } else {
            const pair = relation.join_pairs.find(p => p.local.table === relation.source_table
                && p.remote.table === relation.target_table);
            edges.push({...base, id: relation.id, source: relation.source_table,
                target: relation.target_table, sourceColumn: pair?.local.column,
                targetColumn: pair?.remote.column});
        }
    }
    return edges;
}

export function neighbors(id, edges) {
    const result = new Set([id]);
    for (const edge of edges) {
        if (edge.source === id) result.add(edge.target);
        if (edge.target === id) result.add(edge.source);
        const relation = edge.kind === 'orm' ? edge.detail : null;
        if (relation && (relation.source_table === id || relation.target_table === id)) {
            result.add(relation.source_table);
            result.add(relation.target_table);
            if (relation.secondary) result.add(relation.secondary);
        }
    }
    return result;
}

export function matchesTable(table, query, models) {
    const text = [table.namespace, table.schema_name, table.name,
        ...table.columns.map(c => c.name),
        ...models.filter(m => m.table_ids.includes(table.id)).map(m => m.class_name)].join(' ');
    return text.toLocaleLowerCase().includes(query.toLocaleLowerCase());
}

export function layoutTables(tables, edges, heights) {
    // Place neighboring tables together, with deterministic order and balanced columns.
    const known = new Map(tables.map(t => [t.id, t]));
    const adjacency = new Map(tables.map(t => [t.id, new Set()]));
    for (const edge of edges) {
        if (known.has(edge.source) && known.has(edge.target) && edge.source !== edge.target) {
            adjacency.get(edge.source).add(edge.target);
            adjacency.get(edge.target).add(edge.source);
        }
    }
    const compare = (a, b) => adjacency.get(b).size - adjacency.get(a).size || a.localeCompare(b);
    const visited = new Set(), ordered = [];
    for (const root of [...known.keys()].sort(compare)) {
        if (visited.has(root)) continue;
        const queue = [root]; visited.add(root);
        for (let i = 0; i < queue.length; i++) {
            const id = queue[i]; ordered.push(id);
            for (const other of [...adjacency.get(id)].sort(compare)) {
                if (!visited.has(other)) { visited.add(other); queue.push(other); }
            }
        }
    }
    const columns = Math.max(1, Math.min(6, Math.ceil(Math.sqrt(tables.length * 0.8))));
    const bottoms = Array(columns).fill(50), positions = new Map();
    for (const id of ordered) {
        const col = bottoms.indexOf(Math.min(...bottoms));
        positions.set(id, {x: 60 + col * 410, y: bottoms[col]});
        bottoms[col] += (heights.get(id) || 100) + 90;
    }
    return positions;
}

export function edgePath(start, end, lane = 0, self = false) {
    if (self) {
        const reach = 70 + lane * 18;
        return `M ${start.x} ${start.y} C ${start.x + reach} ${start.y - 65}, ${end.x + reach} ${end.y + 65}, ${end.x} ${end.y}`;
    }
    const bend = Math.max(65, Math.abs(end.x - start.x) * 0.45) + lane * 16;
    return `M ${start.x} ${start.y} C ${start.x + bend} ${start.y}, ${end.x - bend} ${end.y}, ${end.x} ${end.y}`;
}
