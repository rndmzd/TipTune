import fs from 'node:fs/promises';
import { spawnSync } from 'node:child_process';

const ROOT = new URL('..', import.meta.url);

function usage(exitCode = 1) {
  // Intentionally no extra comments/doc blocks per repo style.
  const msg = [
    'Usage: node ./scripts/bump-minor.mjs [--dry-run] [--allow-dirty] [--no-commit] [--no-tag]',
    '',
    'Bumps x.y.z -> x.(y+1).0 in:',
    '  - src-tauri/tauri.conf.json',
    '  - src-tauri/Cargo.toml',
  ].join('\n');
  console.log(msg);
  process.exit(exitCode);
}

function parseArgs(argv) {
  const out = {
    dryRun: false,
    allowDirty: false,
    noCommit: false,
    noTag: false,
  };

  for (const a of argv) {
    if (a === '--dry-run') out.dryRun = true;
    else if (a === '--allow-dirty') out.allowDirty = true;
    else if (a === '--no-commit') out.noCommit = true;
    else if (a === '--no-tag') out.noTag = true;
    else if (a === '-h' || a === '--help') usage(0);
    else usage(1);
  }

  return out;
}

function sh(cmd, args, opts) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', ...opts });
  if (r.error) throw r.error;
  if (typeof r.status === 'number' && r.status !== 0) {
    const stderr = (r.stderr || '').trim();
    const stdout = (r.stdout || '').trim();
    const extra = [stderr, stdout].filter(Boolean).join('\n');
    throw new Error(`Command failed: ${cmd} ${args.join(' ')}${extra ? `\n${extra}` : ''}`);
  }
  return r;
}

function parseSemver(v) {
  const m = String(v).trim().match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!m) throw new Error(`Invalid version: ${v}`);
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3]) };
}

function bumpMinor(v) {
  const { major, minor } = parseSemver(v);
  return `${major}.${minor + 1}.0`;
}

function extractJsonVersion(text) {
  const m = text.match(/\n\s*"version"\s*:\s*"(\d+\.\d+\.\d+)"\s*,?/);
  if (!m) throw new Error('Could not find "version" in tauri.conf.json');
  return m[1];
}

function replaceJsonVersion(text, next) {
  const cur = extractJsonVersion(text);
  const out = text.replace(
    new RegExp(`(\\n\\s*"version"\\s*:\\s*")${cur.replaceAll('.', '\\.')}("\\s*,?)`),
    `$1${next}$2`
  );
  if (out === text) throw new Error('Failed to update version in tauri.conf.json');
  return out;
}

function extractCargoVersion(text) {
  const m = text.match(/^version\s*=\s*"(\d+\.\d+\.\d+)"\s*$/m);
  if (!m) throw new Error('Could not find package version in Cargo.toml');
  return m[1];
}

function replaceCargoVersion(text, next) {
  const cur = extractCargoVersion(text);
  const out = text.replace(
    new RegExp(`^(version\\s*=\\s*")${cur.replaceAll('.', '\\.')}("\\s*)$`, 'm'),
    `$1${next}$2`
  );
  if (out === text) throw new Error('Failed to update version in Cargo.toml');
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  const tauriConfPath = new URL('./src-tauri/tauri.conf.json', ROOT);
  const cargoTomlPath = new URL('./src-tauri/Cargo.toml', ROOT);

  if (!args.allowDirty) {
    const st = sh('git', ['status', '--porcelain'], { cwd: ROOT });
    if ((st.stdout || '').trim() !== '') {
      throw new Error('Working tree is not clean. Commit/stash changes, or pass --allow-dirty.');
    }
  }

  const tauriConfText = await fs.readFile(tauriConfPath, 'utf8');
  const cargoTomlText = await fs.readFile(cargoTomlPath, 'utf8');

  const v1 = extractJsonVersion(tauriConfText);
  const v2 = extractCargoVersion(cargoTomlText);
  if (v1 !== v2) {
    throw new Error(`Version mismatch: tauri.conf.json=${v1} Cargo.toml=${v2}`);
  }

  const next = bumpMinor(v1);

  const nextTauriConf = replaceJsonVersion(tauriConfText, next);
  const nextCargoToml = replaceCargoVersion(cargoTomlText, next);

  console.log(`Current version: ${v1}`);
  console.log(`Next version:    ${next}`);

  if (args.dryRun) {
    console.log('Dry run: no files modified, no git commands executed.');
    return;
  }

  await fs.writeFile(tauriConfPath, nextTauriConf, 'utf8');
  await fs.writeFile(cargoTomlPath, nextCargoToml, 'utf8');

  sh('git', ['add', 'src-tauri/tauri.conf.json', 'src-tauri/Cargo.toml'], { cwd: ROOT });

  if (!args.noCommit) {
    sh('git', ['commit', '-m', `Bump version to v${next}`], { cwd: ROOT });
  }

  if (!args.noTag) {
    sh('git', ['tag', `v${next}`], { cwd: ROOT });
  }

  console.log('Done.');
}

main().catch((e) => {
  console.error(e?.message ? e.message : String(e));
  process.exit(1);
});
