import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {schema} from '../demo/schema.mjs';

export const demoDirectory = new URL('../public/demo/', import.meta.url);
const source = new URL('../../src/laranex_schema_viewer/static/', import.meta.url);

export async function prepareDemo() {
    await mkdir(demoDirectory, {recursive: true});
    for (const filename of ['index.html', 'styles.css', 'graph.js', 'app.js']) {
        let content = await readFile(new URL(filename, source), 'utf8');
        if (filename === 'index.html') {
            content = content.replaceAll('href="/', 'href="./').replaceAll('src="/', 'src="./');
            content = content.replace('<title>Laranex Schema Viewer</title>', '<title>Synthetic workspace · Laranex Schema Viewer</title>');
        }
        if (filename === 'app.js') {
            if (!content.includes("fetch('/snapshot.json')")) throw new Error('Viewer snapshot loading changed; update the static demo adapter');
            content = content.replace("fetch('/snapshot.json')", "fetch('./snapshot.json')");
        }
        await writeFile(new URL(filename, demoDirectory), content);
    }
    await writeFile(new URL('snapshot.json', demoDirectory), JSON.stringify(schema, null, 2) + '\n');
    return schema;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    await prepareDemo();
    console.log(`Prepared synthetic demo: ${schema.tables.length} tables, ${schema.relationships.length} ORM declarations`);
}
