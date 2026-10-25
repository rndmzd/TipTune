import { useId, useRef, useState } from 'react';

type SupportReference = {
  support_id?: unknown;
  error_code?: unknown;
  report_state?: unknown;
};

export function SupportDetails({ support, onStatus }: { support: SupportReference; onStatus: (message: string) => void }) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [copyResult, setCopyResult] = useState<{ id: string; message: string } | null>(null);
  const supportId = typeof support.support_id === 'string' && /^TT-[a-f0-9]{32}$/.test(support.support_id)
    ? support.support_id : '';
  if (!supportId) return null;

  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(supportId);
      setCopyResult({ id: supportId, message: 'Copied.' });
      onStatus('Support ID copied.');
    } catch {
      setCopyResult({ id: supportId, message: 'Copy the selected support ID manually.' });
      input.current?.focus();
      input.current?.select();
      onStatus('Copy is unavailable here. The support ID is selected so you can copy it manually.');
    }
  }

  const explanation = support.report_state === 'queued'
    ? 'A technical report was queued. Share this support ID when reporting the problem.'
    : support.report_state === 'disabled'
      ? 'Automatic reporting was off for this error. This support ID is saved locally.'
      : support.report_state === 'limited' || support.report_state === 'unavailable'
        ? 'An automatic report could not be queued. This support ID is saved locally.'
        : 'Share this support ID when reporting the problem.';

  return (
    <div style={{ marginTop: 12 }}>
      <label htmlFor={inputId}>Support ID</label>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginTop: 4 }}>
        <input id={inputId} ref={input} readOnly value={supportId}
          style={{ flex: '1 1 300px', minWidth: 0, maxWidth: '100%', fontFamily: 'monospace' }} />
        <button type="button" aria-label={`Copy support ID ${supportId}`} onClick={copy}>Copy support ID</button>
        {copyResult?.id === supportId && <span className="muted">{copyResult.message}</span>}
      </div>
      <p className="muted" style={{ marginTop: 6 }}>{explanation}</p>
    </div>
  );
}
