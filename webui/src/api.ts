export type ApiOk<T> = { ok: true } & T;
export type ApiErr = { ok: false; error?: string };

const DEFAULT_TIMEOUT_MS = 8000;

export function isTauriRuntime(): boolean {
  const w: any = window as any;
  return !!(w && (w.__TAURI_INTERNALS__ || w.__TAURI__));
}

function getApiBase(): string {
  if (isTauriRuntime()) return 'http://127.0.0.1:8765';
  return '';
}

function parseApiJson(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: text };
  }
}

export async function apiTextResponse(
  path: string,
  opts?: RequestInit,
  timeoutMs?: number,
): Promise<{ text: string; headers: Headers }> {
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
    const text = await r.text();
    if (!r.ok) {
      throw new Error(parseApiJson(text)?.error || `HTTP ${r.status}`);
    }
    return { text, headers: r.headers };
  } finally {
    window.clearTimeout(tmr);
  }
}

export async function apiJson<T>(path: string, opts?: RequestInit, timeoutMs?: number): Promise<T> {
  const { text } = await apiTextResponse(path, opts, timeoutMs);
  const j = parseApiJson(text);
  if (j?.ok === false) throw new Error(j.error || 'Request failed');
  return j as T;
}

export function sseUrl(path: string): string {
  return getApiBase() + path;
}
