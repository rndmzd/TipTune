export type ApiOk<T> = { ok: true } & T;
export type ApiErr = { ok: false; error?: string };

const DEFAULT_TIMEOUT_MS = 8000;

function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

function getApiBase(): string {
  if (isTauriRuntime()) return 'http://127.0.0.1:8765';
  return '';
}

export async function apiJson<T>(
  path: string,
  opts?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const base = getApiBase();
  const ctrl = new AbortController();
  const ms = typeof timeoutMs === 'number' && timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS;
  const tmr = window.setTimeout(() => ctrl.abort(), ms);

  try {
    let r: Response;
    try {
      r = await fetch(base + path, { ...(opts ?? {}), signal: ctrl.signal });
    } catch (e: any) {
      const isAbort = e?.name === 'AbortError';
      const hint = base
        ? `Unable to reach TipTune backend at ${base}. Make sure the TipTune sidecar is running and that port is not blocked.`
        : 'Unable to reach TipTune backend. Make sure the TipTune server is running.';
      throw new Error(isAbort ? `Request timed out. ${hint}` : `${e?.message ? e.message : 'Failed to fetch'}. ${hint}`);
    }
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
