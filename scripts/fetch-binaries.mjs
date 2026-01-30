import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import https from 'node:https';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..');

const DEFAULT_FFMPEG_VERSION = '7.1.1';
const YT_DLP_URLS = {
  windows: 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe',
  macos: 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos',
  linux: 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux',
};

function die(msg) {
  console.error(msg);
  process.exit(1);
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function platformKey() {
  const runnerOs = String(process.env.RUNNER_OS || '').trim();
  if (runnerOs === 'Windows') return 'windows';
  if (runnerOs === 'macOS') return 'macos';
  if (runnerOs === 'Linux') return 'linux';

  if (process.platform === 'win32') return 'windows';
  if (process.platform === 'darwin') return 'macos';
  if (process.platform === 'linux') return 'linux';

  die(`Unsupported platform: RUNNER_OS=${runnerOs || '<unset>'} process.platform=${process.platform}`);
}

function fileExistsNonEmpty(p) {
  try {
    const st = fs.statSync(p);
    return st.isFile() && st.size > 0;
  } catch {
    return false;
  }
}

function isLatestSpecifier(v) {
  const s = String(v || '').trim().toLowerCase();
  return s === '' || s === 'latest';
}

function downloadToFile(url, destPath) {
  return new Promise((resolve, reject) => {
    const tmpPath = destPath + `.tmp-${process.pid}-${Date.now()}`;
    const out = fs.createWriteStream(tmpPath);

    const onError = (err) => {
      try {
        out.close();
      } catch {
      }
      try {
        if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
      } catch {
      }
      reject(err);
    };

    const req = https.get(
      url,
      {
        headers: {
          'User-Agent': 'TipTune fetch-binaries (node)',
          Accept: '*/*',
        },
      },
      (res) => {
        const code = res.statusCode || 0;
        if (code >= 300 && code < 400 && res.headers.location) {
          const redirectUrl = (() => {
            try {
              return new URL(String(res.headers.location), url).toString();
            } catch {
              return String(res.headers.location);
            }
          })();
          res.resume();
          out.close();
          try {
            if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
          } catch {
          }
          downloadToFile(redirectUrl, destPath).then(resolve, reject);
          return;
        }

        if (code !== 200) {
          res.resume();
          onError(new Error(`HTTP ${code} for ${url}`));
          return;
        }

        res.pipe(out);
        out.on('finish', () => {
          out.close(() => {
            try {
              fs.renameSync(tmpPath, destPath);
              resolve();
            } catch (err) {
              onError(err);
            }
          });
        });
      },
    );

    req.on('error', onError);
    out.on('error', onError);
  });
}

function sh(cmd, args, opts) {
  execFileSync(cmd, args, { stdio: 'inherit', ...opts });
}

function trySh(cmd, args, opts) {
  try {
    execFileSync(cmd, args, { stdio: 'ignore', ...opts });
    return true;
  } catch {
    return false;
  }
}

function extractZip(zipPath, destDir) {
  ensureDir(destDir);

  if (trySh('tar', ['-xf', zipPath, '-C', destDir])) return;
  if (trySh('unzip', ['-o', zipPath, '-d', destDir])) return;

  if (process.platform === 'win32') {
    const ps = process.env.SystemRoot
      ? path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
      : 'powershell.exe';

    sh(
      ps,
      [
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        `Expand-Archive -LiteralPath ${JSON.stringify(zipPath)} -DestinationPath ${JSON.stringify(destDir)} -Force`,
      ],
    );
    return;
  }

  throw new Error('Unable to extract zip (tar/unzip not available)');
}

function copyFile(src, dst) {
  ensureDir(path.dirname(dst));
  fs.copyFileSync(src, dst);
}

function ytDlpFilename(key) {
  return key === 'windows' ? 'yt-dlp.exe' : 'yt-dlp';
}

async function ensureYtDlp(destDir) {
  const key = platformKey();
  const url = YT_DLP_URLS[key];
  if (!url) throw new Error(`Unsupported platform for yt-dlp download: ${key}`);

  const filename = ytDlpFilename(key);
  const destPath = path.join(destDir, filename);

  if (!fileExistsNonEmpty(destPath)) {
    await downloadToFile(url, destPath);
  }

  if (key !== 'windows') {
    try {
      fs.chmodSync(destPath, 0o755);
    } catch {
    }
  }

  try {
    const env = {
      ...process.env,
      PATH: `${destDir}${path.delimiter}${process.env.PATH || ''}`,
    };
    execFileSync(destPath, ['-U'], {
      cwd: destDir,
      env,
      stdio: 'inherit',
    });
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    console.warn(`yt-dlp update failed: ${msg}`);
  }

  if (key !== 'windows') {
    try {
      fs.chmodSync(destPath, 0o755);
    } catch {
    }
  }
}

async function ensureFfmpeg(destDir, ffmpegVersion) {
  const key = platformKey();
  const ffmpegName = key === 'windows' ? 'ffmpeg.exe' : 'ffmpeg';
  const ffprobeName = key === 'windows' ? 'ffprobe.exe' : 'ffprobe';

  const ffmpegDest = path.join(destDir, ffmpegName);
  const ffprobeDest = path.join(destDir, ffprobeName);

  if (fileExistsNonEmpty(ffmpegDest) && fileExistsNonEmpty(ffprobeDest)) return;

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'tiptune-bins-'));
  try {
    if (key === 'linux') {
      const archive = path.join(tmpRoot, 'ffmpeg.tar.xz');
      const resolvedIsLatest = isLatestSpecifier(ffmpegVersion);
      const url = resolvedIsLatest
        ? 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz'
        : `https://www.johnvansickle.com/ffmpeg/old-releases/ffmpeg-${String(ffmpegVersion).trim()}-amd64-static.tar.xz`;
      await downloadToFile(url, archive);
      sh('tar', ['-xJf', archive, '-C', tmpRoot]);

      const extractedDir = fs
        .readdirSync(tmpRoot)
        .map((n) => path.join(tmpRoot, n))
        .find((p) => {
          try {
            return fs.statSync(p).isDirectory() && path.basename(p).startsWith('ffmpeg-');
          } catch {
            return false;
          }
        });

      if (!extractedDir) throw new Error('Failed to locate extracted ffmpeg directory');

      const ffmpegSrc = path.join(extractedDir, 'ffmpeg');
      const ffprobeSrc = path.join(extractedDir, 'ffprobe');
      if (!fileExistsNonEmpty(ffmpegSrc) || !fileExistsNonEmpty(ffprobeSrc)) {
        throw new Error('Extracted ffmpeg/ffprobe not found');
      }

      copyFile(ffmpegSrc, ffmpegDest);
      copyFile(ffprobeSrc, ffprobeDest);
    } else if (key === 'macos') {
      const ffmpegZip = path.join(tmpRoot, 'ffmpeg.zip');
      const ffprobeZip = path.join(tmpRoot, 'ffprobe.zip');

      const resolvedIsLatest = isLatestSpecifier(ffmpegVersion);
      const ffmpegUrl = resolvedIsLatest
        ? 'https://evermeet.cx/ffmpeg/getrelease/zip'
        : `https://evermeet.cx/pub/ffmpeg/ffmpeg-${String(ffmpegVersion).trim()}.zip`;
      const ffprobeUrl = resolvedIsLatest
        ? 'https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip'
        : `https://evermeet.cx/pub/ffprobe/ffprobe-${String(ffmpegVersion).trim()}.zip`;

      await downloadToFile(ffmpegUrl, ffmpegZip);
      await downloadToFile(ffprobeUrl, ffprobeZip);

      const ffmpegOut = path.join(tmpRoot, 'ffmpeg-out');
      const ffprobeOut = path.join(tmpRoot, 'ffprobe-out');
      ensureDir(ffmpegOut);
      ensureDir(ffprobeOut);

      extractZip(ffmpegZip, ffmpegOut);
      extractZip(ffprobeZip, ffprobeOut);

      const ffmpegSrc = path.join(ffmpegOut, 'ffmpeg');
      const ffprobeSrc = path.join(ffprobeOut, 'ffprobe');
      if (!fileExistsNonEmpty(ffmpegSrc) || !fileExistsNonEmpty(ffprobeSrc)) {
        throw new Error('Unzipped ffmpeg/ffprobe not found');
      }

      copyFile(ffmpegSrc, ffmpegDest);
      copyFile(ffprobeSrc, ffprobeDest);

      try {
        fs.chmodSync(ffmpegDest, 0o755);
        fs.chmodSync(ffprobeDest, 0o755);
      } catch {
      }
    } else if (key === 'windows') {
      const zip = path.join(tmpRoot, 'ffmpeg.zip');
      const resolvedIsLatest = isLatestSpecifier(ffmpegVersion);
      const url = resolvedIsLatest
        ? 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        : `https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-${String(ffmpegVersion).trim()}-essentials_build.zip`;
      await downloadToFile(url, zip);

      const outDir = path.join(tmpRoot, 'out');
      ensureDir(outDir);
      extractZip(zip, outDir);

      const ffmpegSrc = findFirstMatch(outDir, /\\bin\\ffmpeg\.exe$/i);
      const ffprobeSrc = findFirstMatch(outDir, /\\bin\\ffprobe\.exe$/i);
      if (!ffmpegSrc || !ffprobeSrc) {
        throw new Error('Unzipped ffmpeg/ffprobe not found (expected */bin/ffmpeg.exe and */bin/ffprobe.exe)');
      }

      copyFile(ffmpegSrc, ffmpegDest);
      copyFile(ffprobeSrc, ffprobeDest);
    } else {
      throw new Error(`Unsupported platform key: ${key}`);
    }
  } finally {
    try {
      fs.rmSync(tmpRoot, { recursive: true, force: true });
    } catch {
    }
  }
}

function findFirstMatch(rootDir, regex) {
  const stack = [rootDir];
  while (stack.length) {
    const cur = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(cur, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const p = path.join(cur, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.isFile()) {
        const norm = p.replace(/\//g, '\\');
        if (regex.test(norm)) return p;
      }
    }
  }
  return null;
}

async function main() {
  const key = platformKey();
  const ffmpegVersion = String(process.env.FFMPEG_VERSION || '').trim() || DEFAULT_FFMPEG_VERSION;

  const destDir = path.join(repoRoot, 'src-tauri', 'resources', 'bin', key);
  ensureDir(destDir);

  await ensureFfmpeg(destDir, ffmpegVersion);
  await ensureYtDlp(destDir);

  const files = fs.readdirSync(destDir);
  console.log(`Fetched binaries into: ${destDir}`);
  for (const f of files) console.log(`- ${f}`);
}

main().catch((e) => {
  console.error(e?.stack || e?.message || String(e));
  process.exit(1);
});
