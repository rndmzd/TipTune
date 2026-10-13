// Keep source maps private and upload the exact artifacts whose debug IDs ship.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { SentryCli } from '@sentry/cli';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(repo, 'webui', 'dist');
const storage = path.join(repo, '.cache', 'sentry-source-maps');
const latest = path.join(storage, 'latest.json');
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const assetName = /^assets\/[A-Za-z0-9_.-]+\.js$/;
const hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const release = 'tiptune@' + JSON.parse(fs.readFileSync(path.join(repo, 'package.json'), 'utf8')).version;

function filesIn(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.isSymbolicLink()) throw new Error('Source-map artifacts must not be symbolic links.');
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? filesIn(target) : [target];
  }).sort();
}

function checkPair(code, mapBytes, name) {
  const ids = [...code.toString('utf8').matchAll(/^\/\/# debugId=([a-f0-9-]+)\s*$/gm)];
  const map = JSON.parse(mapBytes.toString('utf8'));
  const id = ids[0]?.[1];
  if (ids.length !== 1 || !uuid.test(id || '') || !code.toString('utf8').includes('sentry-dbid-' + id) ||
      map.debug_id !== id || (map.debugId && map.debugId !== id)) {
    throw new Error(`Missing or mismatched debug ID: ${name}`);
  }
  if (/^\/\/[#@]\s*sourceMappingURL=/m.test(code.toString('utf8'))) {
    throw new Error(`A public source-map reference survived: ${name}`);
  }
  if (map.version !== 3 || !map.mappings || !Array.isArray(map.sources) || !Array.isArray(map.sourcesContent)) {
    throw new Error(`Incomplete source map: ${name}`);
  }
  for (const source of [...map.sources, map.sourceRoot || '']) {
    if (typeof source !== 'string' || /^(?:[a-z]+:|\/|\\)/i.test(source)) {
      throw new Error(`Absolute source paths are not allowed: ${name}`);
    }
  }
  return id;
}

function writeImmutable(target, bytes) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  if (fs.existsSync(target)) {
    if (!fs.readFileSync(target).equals(bytes)) throw new Error('An archived source-map artifact changed.');
  } else {
    fs.writeFileSync(target, bytes, { flag: 'wx' });
  }
}

function prepare() {
  const pairs = filesIn(dist).filter((file) => file.endsWith('.js')).map((file) => {
    const name = path.relative(dist, file).replaceAll(path.sep, '/');
    if (!assetName.test(name)) throw new Error(`Unexpected JavaScript asset: ${name}`);
    const code = fs.readFileSync(file);
    const mapPath = file + '.map';
    let map = fs.readFileSync(mapPath);
    const parsedMap = JSON.parse(map.toString('utf8'));
    // Vite's preload transform may regenerate a chunk's map after the Sentry
    // hook. Restore only missing metadata from its existing injection; never
    // inject or change JavaScript after Vite computes the asset filenames.
    if (!parsedMap.debug_id && !parsedMap.debugId) {
      const id = /^\/\/# debugId=([a-f0-9-]+)\s*$/m.exec(code.toString('utf8'))?.[1];
      parsedMap.debug_id = parsedMap.debugId = id;
      map = Buffer.from(JSON.stringify(parsedMap));
    }
    const debugId = checkPair(code, map, name);
    return { name, code, map, mapPath, debugId };
  });
  if (!pairs.length) throw new Error('No built JavaScript assets were found.');
  const assets = pairs.map(({ name, code, map, debugId }) => ({ name, debugId, codeHash: hash(code), mapHash: hash(map) }));
  const manifest = { release, assets };
  const bytes = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
  const build = hash(bytes);
  const archive = path.join(storage, build);
  for (const pair of pairs) {
    writeImmutable(path.join(archive, pair.name), pair.code);
    writeImmutable(path.join(archive, pair.name + '.map'), pair.map);
  }
  writeImmutable(path.join(archive, 'manifest.json'), bytes);
  // These paths were read directly from dist above; no computed recursive removal.
  for (const pair of pairs) fs.unlinkSync(pair.mapPath);
  fs.writeFileSync(latest + '.tmp', JSON.stringify({ build }) + '\n');
  fs.renameSync(latest + '.tmp', latest);
  verify();
}

function verify() {
  const { build } = JSON.parse(fs.readFileSync(latest, 'utf8'));
  if (!/^[a-f0-9]{64}$/.test(build)) throw new Error('Invalid source-map archive identifier.');
  const archive = path.join(storage, build);
  const manifestBytes = fs.readFileSync(path.join(archive, 'manifest.json'));
  if (hash(manifestBytes) !== build) throw new Error('The source-map manifest changed.');
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  if (manifest.release !== release || !Array.isArray(manifest.assets) || !manifest.assets.length) {
    throw new Error('Source-map archive does not match the current app version. Rebuild the Web UI.');
  }
  const publicFiles = filesIn(dist);
  if (publicFiles.some((file) => file.endsWith('.map'))) throw new Error('A source map remains in the public Web UI.');
  const names = publicFiles.filter((file) => file.endsWith('.js')).map((file) => path.relative(dist, file).replaceAll(path.sep, '/'));
  if (JSON.stringify(names) !== JSON.stringify(manifest.assets.map((asset) => asset.name))) {
    throw new Error('Archived JavaScript assets do not match the current Web UI.');
  }
  for (const asset of manifest.assets) {
    if (!assetName.test(asset.name)) throw new Error('Invalid source-map asset path.');
    const code = fs.readFileSync(path.join(archive, asset.name));
    const map = fs.readFileSync(path.join(archive, asset.name + '.map'));
    if (hash(code) !== asset.codeHash || hash(map) !== asset.mapHash ||
        !code.equals(fs.readFileSync(path.join(dist, asset.name))) || checkPair(code, map, asset.name) !== asset.debugId) {
      throw new Error(`Source map no longer matches the distributed JavaScript: ${asset.name}`);
    }
  }
  console.log(`Verified ${manifest.assets.length} private source maps for ${release}; no maps in webui/dist.`);
  return archive;
}

async function upload() {
  const archive = verify();
  const org = process.env.SENTRY_ORG?.trim();
  const project = process.env.SENTRY_PROJECT?.trim();
  if (!process.env.SENTRY_AUTH_TOKEN || !org || !project) {
    throw new Error('Private upload requires SENTRY_AUTH_TOKEN, SENTRY_ORG, and SENTRY_PROJECT in the build environment.');
  }
  if (![org, project].every((value) => /^[a-z0-9_-]+$/.test(value))) throw new Error('Invalid Sentry organization or project slug.');
  const cli = new SentryCli(null, { url: 'https://sentry.io/', authToken: process.env.SENTRY_AUTH_TOKEN, org, project });
  // The Vite plugin already injected IDs. Injecting again can break matching.
  await cli.execute(['sourcemaps', 'upload', '--release', release, '--url-prefix', 'app:///assets',
    '--validate', '--strict', '--wait-for', '60', path.join(archive, 'assets')], true);
  console.log(`Sentry processed the source-map upload for ${release}.`);
}

try {
  const command = process.argv[2];
  if (command === 'prepare') prepare();
  else if (command === 'verify') verify();
  else if (command === 'upload') await upload();
  else throw new Error('Usage: node scripts/sentry-source-maps.mjs prepare|verify|upload');
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Source-map operation failed.');
  process.exitCode = 1;
}
