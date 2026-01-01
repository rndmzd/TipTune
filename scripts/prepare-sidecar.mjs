import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';

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

function ensureSidecarBuilt(sidecarName, extension) {
  const distPath = resolveSidecarDistPath(sidecarName, extension);
  if (fs.existsSync(distPath)) {
    return distPath;
  }

  const specPath = path.join(repoRoot, `${sidecarName}.spec`);
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
fs.copyFileSync(distPath, destPath);

console.log(`Prepared sidecar: ${destPath}`);
