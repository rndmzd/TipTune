import { sseUrl } from './api';

declare const __APP_VERSION__: string;
const debugIdPattern = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
type CodeFrame = { filename: string; lineno: number; colno: number; abs_path?: string; debug_id?: string };

export type ReportingStatus = {
  ok: true;
  enabled: boolean;
  configured: boolean;
  pending: number;
  last_sent_at: number | null;
  error: string | null;
};

let allowed = false;
let component: 'webui' | 'overlay' = 'webui';
let installed = false;
let initialization: Promise<ReportingStatus | null> | null = null;
const recent = new Map<string, number>();

async function localFetch(path: string, options?: RequestInit) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 3000);
  try {
    return await fetch(sseUrl(path), { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

export function setReportingConsent(enabled: boolean) {
  allowed = enabled;
  if (!enabled) recent.clear();
}

export async function refreshReportingConsent(): Promise<ReportingStatus | null> {
  try {
    const response = await localFetch('/api/error-reporting', { cache: 'no-store' });
    if (!response.ok) throw new Error('Reporting status unavailable');
    const status: ReportingStatus = await response.json();
    setReportingConsent(status.ok === true && status.enabled === true && status.configured === true);
    return status;
  } catch {
    setReportingConsent(false);
    return null;
  }
}

function codeFrame(location: string, line: number, column: number): CodeFrame | null {
  try {
    const url = new URL(location);
    if (url.origin !== window.location.origin || !/^\/(?:assets\/[\w.-]+\.js|src\/[\w./-]+\.[jt]sx?)$/.test(url.pathname)) return null;
    return { filename: url.pathname.split('/').pop()!, lineno: line, colno: column,
      ...(/^\/assets\/[\w.-]+\.js$/.test(url.pathname) ? { abs_path: 'app://' + url.pathname } : {}) };
  } catch {
    return null;
  }
}

function stackFrames(stack: string) {
  const frames: CodeFrame[] = [];
  for (const stackLine of stack.split('\n').slice(0, 40)) {
    const match = /((?:https?|tauri):\/\/[^\s)]+):(\d+):(\d+)/.exec(stackLine);
    if (!match) continue;
    const frame = codeFrame(match[1], Number(match[2]), Number(match[3]));
    if (frame) frames.push(frame);
  }
  return frames;
}

function bundledDebugIds() {
  const injected = (window as Window & { _sentryDebugIds?: Record<string, unknown> })._sentryDebugIds;
  const ids = new Map<string, string>();
  for (const [stack, id] of Object.entries(injected || {}).slice(0, 200)) {
    if (typeof id !== 'string' || !debugIdPattern.test(id)) continue;
    // The injection's first bundled frame identifies its own chunk. Caller frames
    // may belong to other chunks and must not inherit this debug ID.
    const frame = stackFrames(stack).find((item) => item.abs_path);
    if (frame?.abs_path) ids.set(frame.abs_path, id);
  }
  return ids;
}

export function frontendErrorPayload(error: unknown, location?: string, line?: number, column?: number) {
  const candidate = error instanceof Error ? error : null;
  const knownTypes = ['Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'URIError', 'EvalError'];
  const type = candidate && knownTypes.includes(candidate.name) ? candidate.name : 'JavaScriptError';
  // Only our bundled code locations survive. Error messages and raw stacks never leave the browser.
  const frames = stackFrames(String(candidate?.stack || '')).reverse();
  if (!frames.length && location && line) {
    const frame = codeFrame(location, line, column || 1);
    if (frame) frames.push(frame);
  }
  const ids = bundledDebugIds();
  for (const frame of frames) {
    const id = frame.abs_path && ids.get(frame.abs_path);
    if (id) frame.debug_id = id;
  }
  return { component, type, frames, release: 'tiptune@' + __APP_VERSION__ };
}

export function reportFrontendError(error: unknown, location?: string, line?: number, column?: number) {
  if (!allowed) return;
  try {
    const payload = frontendErrorPayload(error, location, line, column);
    const signature = JSON.stringify(payload);
    const now = Date.now();
    if (now - (recent.get(signature) || 0) < 60000) return;
    for (const [key, when] of recent) if (now - when >= 60000) recent.delete(key);
    if (recent.size >= 100) return;
    recent.set(signature, now);
    void localFetch('/api/error-reporting/event', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }).catch(() => {});
  } catch {
    // An error in the reporter must never become another report.
  }
}

export function installErrorReporting(source: 'webui' | 'overlay') {
  if (installed) return initialization!;
  installed = true;
  component = source;
  window.addEventListener('error', (event) => reportFrontendError(event.error, event.filename, event.lineno, event.colno));
  window.addEventListener('unhandledrejection', (event) => reportFrontendError(event.reason));
  initialization = refreshReportingConsent();
  window.setInterval(() => { void refreshReportingConsent(); }, 15000);
  return initialization;
}
