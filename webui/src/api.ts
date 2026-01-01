export type ApiOk<T> = { ok: true } & T;
export type ApiErr = { ok: false; error?: string };

const DEFAULT_TIMEOUT_MS = 5000;

function getApiBase(): string {
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  if ((protocol === 'http:' || protocol === 'https:') && hostname !== 'tauri.localhost') return '';
  return 'http://127.0.0.1:8765';
}

export async function apiJson<T>(
  path: string,
  opts?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const ctrl = new AbortController();
  const ms = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS;
  const tmr = window.setTimeout(() => ctrl.abort(), ms);

  try {
    const r = await fetch(getApiBase() + path, { ...(opts ?? {}), signal: ctrl.signal });
    const t = await r.text();

    let j: any;
    try {
      j = JSON.parse(t);
    } catch {
      j = { ok: false, error: t };
    }

    if (!r.ok) {
      throw new Error(j?.error || `HTTP ${r.status}`);
    }
    if (j && j.ok === false) {
      throw new Error(j?.error || 'Request failed');
    }

    return j as T;
  } finally {
    window.clearTimeout(tmr);
  }
}

export function sseUrl(path: string): string {
  return getApiBase() + path;
}
