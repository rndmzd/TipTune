import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

type ConfigResp = { ok: true; config: Record<string, Record<string, string>> };

function asBool(v: unknown): boolean {
  if (typeof v === 'boolean') return v;
  const s = String(v ?? '').trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'on';
}

export function SetupPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const rerun = searchParams.get('rerun') === '1' || searchParams.get('rerun') === 'true';

  const titleSuffix = useMemo(() => (rerun ? ' (rerun)' : ''), [rerun]);

  const [statusText, setStatusText] = useState<'loading' | 'complete' | 'incomplete'>('loading');
  const [statusMsg, setStatusMsg] = useState('');

  async function refresh() {
    const data = await apiJson<ConfigResp>('/api/config');
    const setupComplete = asBool((data.config?.General || {}).setup_complete);
    setStatusText(setupComplete ? 'complete' : 'incomplete');
  }

  useEffect(() => {
    refresh().catch(() => setStatusText('incomplete'));
  }, []);

  return (
    <>
      <HeaderBar
        title={`Setup Wizard${titleSuffix}`}
        right={
          <div className="muted">
            <a href="/?dashboard=1">Dashboard</a>
          </div>
        }
      />

      <div className="card">
        <h2>
          Setup status: <span className="pill">{statusText}</span>
        </h2>
        <div className="muted">Use the settings page to enter your settings. When you're done, mark setup as complete.</div>
        <div className="actions">
          <button type="button" onClick={() => navigate('/settings?dashboard=1')}>
            Open Settings
          </button>
          <button
            type="button"
            onClick={async () => {
              setStatusMsg('Saving...');
              try {
                await apiJson('/api/config', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ General: { setup_complete: 'true' } }),
                });
                setStatusMsg('Setup marked complete.');
                navigate('/');
              } catch (e: any) {
                setStatusMsg(`Error: ${e?.message ? e.message : String(e)}`);
              }
            }}
          >
            Mark Setup Complete
          </button>
        </div>
        <div className="muted">{statusMsg}</div>
      </div>
    </>
  );
}
