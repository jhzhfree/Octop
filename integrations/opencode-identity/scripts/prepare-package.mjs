// npm packs a linked local dependency using paths containing "..". Materialize
// only published files, so the TGZ installs independently on Windows as well.
import { cp, mkdir, readFile, realpath, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
const source = await realpath(path.resolve(root, manifest.dependencies['@akb/agent-identity-sdk'].slice(5)));
const destination = path.join(root, 'node_modules/@akb/agent-identity-sdk');
const staging = path.join(root, `node_modules/@akb/.sdk-package-${randomUUID()}`);
async function publishedFiles(from, to) {
  const pkg = JSON.parse(await readFile(path.join(from, 'package.json'), 'utf8'));
  await mkdir(to, { recursive: true });
  for (const entry of ['package.json', ...pkg.files]) {
    if (path.isAbsolute(entry) || entry.split(/[\\/]/).includes('..')) throw new Error('Invalid published package path');
    await cp(path.join(from, entry), path.join(to, entry), { recursive: true, dereference: true });
  }
}
try {
  await publishedFiles(source, staging);
  await publishedFiles(await realpath(path.join(source, 'node_modules/@akb/agent-keygen')),
    path.join(staging, 'node_modules/@akb/agent-keygen'));
  // rm removes a symlink itself, never the source directory it points at.
  await rm(destination, { recursive: true, force: true });
  await rename(staging, destination);
} finally { await rm(staging, { recursive: true, force: true }); }
