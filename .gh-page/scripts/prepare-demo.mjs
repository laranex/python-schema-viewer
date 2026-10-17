import {copyFile, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

export const demoDirectory = new URL('../public/demo/', import.meta.url);
const viewer = new URL('../../src/laranex_schema_viewer/static/viewer/', import.meta.url);
const schemaFile = new URL('../demo/schema.json', import.meta.url);

// The packaged viewer, exactly as `laranex-schema-viewer serve` serves it, with a synthetic laranex-schema document.
export async function prepareDemo() {
    await rm(demoDirectory, {recursive: true, force: true});
    const manifest = JSON.parse(await readFile(new URL('manifest.json', viewer), 'utf8'));
    for (const {path} of manifest.files) {
        const target = new URL(path, demoDirectory);
        await mkdir(dirname(fileURLToPath(target)), {recursive: true});
        await copyFile(new URL(path, viewer), target);
    }
    const schema = JSON.parse(await readFile(schemaFile, 'utf8'));
    await writeFile(new URL('snapshot.json', demoDirectory), JSON.stringify(schema, null, 2) + '\n');
    return {schema, manifest};
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    const {schema, manifest} = await prepareDemo();
    const tables = schema.connections.reduce((n, c) => n + c.tables.length, 0);
    console.log(`Prepared synthetic demo with viewer ${manifest.version}: ${tables} tables, ${schema.relationships.length} ORM declarations`);
}
