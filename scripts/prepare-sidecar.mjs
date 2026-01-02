import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync, spawn } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..');

function getTargetTriple() {
  const env = process.env.TAURI_ENV_TARGET_TRIPLE || process.env.TAURI_TARGET_TRIPLE;
  if (typeof env === 'string' && env.trim()) {
    return env.trim();
  }

  try {
    const rustInfo = execSync('rustc -vV', {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    const match = /host: (\S+)/.exec(rustInfo);
    if (match && match[1]) {
      return match[1];
    }
  } catch (err) {
    throw new Error('Unable to determine Rust target triple; set TAURI_ENV_TARGET_TRIPLE or ensure rustc is available.');
  }

  throw new Error('Unable to determine Rust target triple; set TAURI_ENV_TARGET_TRIPLE.');
}

function resolveSidecarDistPath(sidecarName, extension) {
  const candidates = [
    path.join(repoRoot, 'dist', `${sidecarName}${extension}`),
    path.join(repoRoot, 'dist', sidecarName, `${sidecarName}${extension}`),
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return candidates[0];
}

function newestMtimeMs(paths) {
  let newest = 0;
  for (const p of paths) {
    try {
      if (fs.existsSync(p)) {
        const st = fs.statSync(p);
        if (st.mtimeMs > newest) newest = st.mtimeMs;
      }
    } catch {
    }
  }
  return newest;
}

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForReadable(filePath, timeoutMs = 4000) {
  const start = Date.now();
  while (true) {
    try {
      const fd = fs.openSync(filePath, 'r');
      fs.closeSync(fd);
      return;
    } catch (err) {
      const code = err && err.code ? String(err.code) : '';
      if (Date.now() - start > timeoutMs) {
        throw err;
      }
      if (code === 'EACCES' || code === 'EPERM' || code === 'EBUSY') {
        await sleepMs(150);
        continue;
      }
      throw err;
    }
  }
}

function ensureSidecarBuilt(sidecarName, extension) {
  const distPath = resolveSidecarDistPath(sidecarName, extension);
  const specPath = path.join(repoRoot, `${sidecarName}.spec`);
  const forceRebuild = String(process.env.TIPTUNE_FORCE_SIDECAR_REBUILD || '').trim() === '1';
  const sourcePaths = [
    specPath,
    path.join(repoRoot, 'app.py'),
    path.join(repoRoot, 'helpers', '__init__.py'),
    path.join(repoRoot, 'utils', 'runtime_paths.py'),
  ];

  if (fs.existsSync(distPath) && !forceRebuild) {
    try {
      const distStat = fs.statSync(distPath);
      const newestSrc = newestMtimeMs(sourcePaths);
      if (distStat.mtimeMs >= newestSrc && newestSrc > 0) {
        return distPath;
      }
    } catch {
      return distPath;
    }
  }

  const pythonCmd = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

  try {
    execSync(`${pythonCmd} -m PyInstaller ${JSON.stringify(specPath)} --clean`, {
      cwd: repoRoot,
      stdio: 'inherit',
    });
  } catch (err) {
    throw new Error(
      `Failed to build sidecar with PyInstaller. Install build deps (pip install -r requirements.txt -r requirements-build.txt) and run: pyinstaller ${sidecarName}.spec --clean`,
    );
  }

  const distPathAfter = resolveSidecarDistPath(sidecarName, extension);
  if (!fs.existsSync(distPathAfter)) {
    throw new Error(`PyInstaller completed but expected sidecar was not found at: ${distPathAfter}`);
  }

  return distPathAfter;
}

const sidecarName = 'TipTune';
const extension = process.platform === 'win32' ? '.exe' : '';
const targetTriple = getTargetTriple();
const distPath = ensureSidecarBuilt(sidecarName, extension);

const binariesDir = path.join(repoRoot, 'src-tauri', 'binaries');
fs.mkdirSync(binariesDir, { recursive: true });

const destPath = path.join(binariesDir, `${sidecarName}-${targetTriple}${extension}`);
let shouldCopy = true;
try {
  if (fs.existsSync(destPath)) {
    const srcStat = fs.statSync(distPath);
    const dstStat = fs.statSync(destPath);
    if (dstStat.size === srcStat.size && dstStat.mtimeMs >= srcStat.mtimeMs) {
      shouldCopy = false;
    }
  }
} catch {
  shouldCopy = true;
}

if (shouldCopy) {
  const tmpPath = destPath + `.tmp-${process.pid}-${Date.now()}`;
  try {
    fs.copyFileSync(distPath, tmpPath);
    const desiredMode = (() => {
      if (process.platform === 'win32') return null;
      try {
        const srcMode = fs.statSync(distPath).mode & 0o777;
        return srcMode || 0o755;
      } catch {
        return 0o755;
      }
    })();
    try {
      if (desiredMode !== null) fs.chmodSync(tmpPath, desiredMode);
    } catch {
    }
    fs.renameSync(tmpPath, destPath);
    try {
      if (desiredMode !== null) fs.chmodSync(destPath, desiredMode);
    } catch {
    }
  } finally {
    try {
      if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
    } catch {
    }
  }
}

try {
  if (process.platform !== 'win32') {
    const srcMode = fs.statSync(distPath).mode & 0o777;
    fs.chmodSync(destPath, srcMode || 0o755);
  }
} catch {
}

await waitForReadable(destPath, 15000);

console.log(`Prepared sidecar: ${destPath}`);

const shouldSpawnDev = process.argv.includes('--spawn-webui-dev');
if (shouldSpawnDev) {
  try {
    if (process.platform === 'win32') {
      const comspec = process.env.COMSPEC || 'C:\\Windows\\System32\\cmd.exe';
      const child = spawn(comspec, ['/d', '/s', '/c', 'npm run webui:dev'], {
        cwd: repoRoot,
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
      });
      child.unref();
    } else {
      const child = spawn('npm', ['run', 'webui:dev'], {
        cwd: repoRoot,
        detached: true,
        stdio: 'ignore',
      });
      child.unref();
    }
    console.log('Spawned webui dev server (detached).');
  } catch (err) {
    console.warn('Failed to spawn webui dev server:', err);
  }
}
