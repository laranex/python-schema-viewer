// Entirely synthetic project workspace. Never import application/reference metadata.
const tid = name => `demo:${name}`;
const mid = name => `model:${name}`;
const col = (name, type = 'INTEGER', options = {}) => ({
    name, type, timezone: null, nullable: false, primary_key: name === 'id',
    autoincrement: 'auto', default: null, server_default: null, comment: null, ...options,
});
const fk = (table, column, target) => ({
    id: `fk:${table}:${column}`, name: `fk_${table}_${column}`, columns: [column],
    target_table: tid(target), target_columns: ['id'], on_delete: 'RESTRICT',
    on_update: null, deferrable: null, initially: null,
});
const index = (name, columns, unique = false, options = {}) => ({
    name, columns, expressions: [...columns], unique, predicates: {}, included_columns: {}, methods: {}, ...options,
});
const table = (name, columns, foreign_keys = [], indexes = []) => ({
    id: tid(name), namespace: 'workspace', name, schema_name: null,
    comment: 'Synthetic public documentation example', columns: [col('id'), ...columns],
    primary_key: ['id'], indexes, foreign_keys, constraints: [],
});
const pair = (localTable, localColumn, remoteTable, remoteColumn) => ({
    local: {table: tid(localTable), column: localColumn},
    remote: {table: tid(remoteTable), column: remoteColumn},
});
function relation(source, name, target, direction, join_pairs, secondary = null) {
    return {
        id: `orm:${source}:${name}`, name, model: mid(source), target_model: mid(target),
        source_table: tid(source), target_table: tid(target), direction,
        uselist: direction !== 'MANYTOONE', viewonly: false, back_populates: null,
        secondary: secondary ? tid(secondary) : null,
        primary_join: join_pairs.map(p => `${p.local.table}.${p.local.column} = ${p.remote.table}.${p.remote.column}`).join(' AND '),
        secondary_join: secondary ? `${secondary}.${target.slice(0, -1)}_id = ${target}.id` : null,
        join_pairs,
    };
}

export const schema = {
    format: 'laranex-schema-viewer', version: 1, scope: 'declared_metadata',
    tables: [
        table('users', [col('email', 'VARCHAR(254)'), col('manager_id', 'INTEGER', {nullable: true}), col('active', 'BOOLEAN')],
            [fk('users', 'manager_id', 'users')],
            [index('uq_users_active_email', ['email'], true, {predicates: {postgresql: 'active = true'}})]),
        table('teams', [col('name', 'VARCHAR(100)')]),
        table('team_members', [col('user_id'), col('team_id')],
            [fk('team_members', 'user_id', 'users'), fk('team_members', 'team_id', 'teams')],
            [index('uq_team_members_pair', ['team_id', 'user_id'], true)]),
        table('projects', [col('name', 'VARCHAR(120)'), col('owner_id'), col('team_id')],
            [fk('projects', 'owner_id', 'users'), fk('projects', 'team_id', 'teams')]),
        table('tasks', [col('title', 'VARCHAR(200)'), col('project_id'), col('assignee_id'), col('parent_id', 'INTEGER', {nullable: true}), col('metadata', 'JSONB')],
            [fk('tasks', 'project_id', 'projects'), fk('tasks', 'assignee_id', 'users'), fk('tasks', 'parent_id', 'tasks')],
            [index('ix_tasks_project', ['project_id']), index('ix_tasks_lower_title', [], false, {expressions: ['lower(title)']})]),
        table('comments', [col('body', 'TEXT'), col('task_id'), col('user_id')],
            [fk('comments', 'task_id', 'tasks'), fk('comments', 'user_id', 'users')]),
        table('tags', [col('name', 'VARCHAR(40)')]),
        table('task_tags', [col('task_id'), col('tag_id')],
            [fk('task_tags', 'task_id', 'tasks'), fk('task_tags', 'tag_id', 'tags')]),
        table('audit_events', [col('actor_id'), col('event', 'VARCHAR(80)'), col('created_at', 'DATETIME', {timezone: true})],
            [fk('audit_events', 'actor_id', 'users')]),
    ],
    models: ['users', 'teams', 'projects', 'tasks', 'comments', 'tags'].map(name => ({
        id: mid(name), class_name: `demo.models.${name.split('_').map(s => s[0].toUpperCase() + s.slice(1)).join('')}Model`,
        table_ids: [tid(name)], primary_key: ['id'], source: null,
    })),
    relationships: [
        relation('users', 'manager', 'users', 'MANYTOONE', [pair('users', 'manager_id', 'users', 'id')]),
        relation('users', 'teams', 'teams', 'MANYTOMANY', [pair('users', 'id', 'team_members', 'user_id'), pair('teams', 'id', 'team_members', 'team_id')], 'team_members'),
        relation('teams', 'members', 'users', 'MANYTOMANY', [pair('teams', 'id', 'team_members', 'team_id'), pair('users', 'id', 'team_members', 'user_id')], 'team_members'),
        relation('projects', 'tasks', 'tasks', 'ONETOMANY', [pair('projects', 'id', 'tasks', 'project_id')]),
        relation('tasks', 'project', 'projects', 'MANYTOONE', [pair('tasks', 'project_id', 'projects', 'id')]),
        relation('tasks', 'assignee', 'users', 'MANYTOONE', [pair('tasks', 'assignee_id', 'users', 'id')]),
        relation('tasks', 'comments', 'comments', 'ONETOMANY', [pair('tasks', 'id', 'comments', 'task_id')]),
        relation('comments', 'task', 'tasks', 'MANYTOONE', [pair('comments', 'task_id', 'tasks', 'id')]),
        relation('tasks', 'tags', 'tags', 'MANYTOMANY', [pair('tasks', 'id', 'task_tags', 'task_id'), pair('tags', 'id', 'task_tags', 'tag_id')], 'task_tags'),
    ],
    diagnostics: [],
};
