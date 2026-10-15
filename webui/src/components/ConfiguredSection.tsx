import { useEffect, useState, type ReactNode } from 'react';

export function ConfiguredSection({ id, title, ready, status, tone = 'neutral', pending = false, actions, feedback, children, className = '' }: {
  id: string; title: string; ready: boolean; status: string;
  tone?: 'neutral' | 'success' | 'warn'; pending?: boolean;
  actions?: ReactNode; feedback?: ReactNode; children: ReactNode; className?: string;
}) {
  const [expanded, setExpanded] = useState(!ready || pending);
  useEffect(() => { setExpanded(!ready || pending); }, [ready, pending]);
  const heading = `${id}-heading`;
  const body = `${id}-body`;
  return <section id={id} className={`card settingsSection settingsDisclosure ${className}`} aria-labelledby={heading} tabIndex={-1}>
    <div className="settingsDisclosureHeader">
      <h2 id={heading}><button type="button" className="settingsDisclosureToggle" aria-expanded={expanded} aria-controls={body}
        onClick={() => setExpanded((previous) => !previous)}>
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" className={expanded ? 'expanded' : ''}>
          <path d="m5 3 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {title}
      </button></h2>
      <span role="status" className={`pill ${pending ? 'pillInfo' : tone === 'success' ? 'pillSuccess' : tone === 'warn' ? 'pillWarn' : 'pillNeutral'}`}>
        {pending ? 'Unsaved changes' : status}
      </span>
      {actions ? <div className="settingsDisclosureActions">{actions}</div> : null}
    </div>
    {feedback}
    <div id={body} className="settingsDisclosureBody" hidden={!expanded}>{children}</div>
  </section>;
}
