import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSlug from 'rehype-slug';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

type UserManualResp = { ok: true; markdown: string };
type HelpSection = { id: string; title: string };

function headingIdFromHash(hash: string): string {
  try {
    return decodeURIComponent(hash.replace(/^#/, ''));
  } catch {
    return '';
  }
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
}

export function HelpPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const outRef = useRef<HTMLDivElement | null>(null);

  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [err, setErr] = useState<string>('');
  const [md, setMd] = useState<string>('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [showBackTop, setShowBackTop] = useState<boolean>(false);
  const [sections, setSections] = useState<HelpSection[]>([]);
  const [currentSection, setCurrentSection] = useState('');
  const hasContent = status === 'ok' && md.trim().length > 0;

  useEffect(() => {
    let cancelled = false;
    setStatus('loading');
    setErr('');
    setMd('');
    setShowBackTop(false);

    async function load() {
      try {
        const data = await apiJson<UserManualResp | null>('/api/help/user-manual');
        if (cancelled) return;
        if (data?.ok !== true || typeof data.markdown !== 'string') {
          throw new Error('The manual response was invalid.');
        }
        setMd(data.markdown);
        setStatus('ok');
      } catch (error) {
        if (cancelled) return;
        setStatus('error');
        setErr(error instanceof Error ? error.message : String(error));
      }
    }

    void load();
    return () => { cancelled = true; };
  }, [loadAttempt]);

  useEffect(() => {
    if (!hasContent) {
      setSections([]);
      setCurrentSection('');
      return;
    }
    const headings = outRef.current?.querySelectorAll<HTMLHeadingElement>('.helpManual h2[id]');
    setSections(Array.from(headings ?? [], (heading) => ({
      id: heading.id,
      title: heading.textContent?.trim() || heading.id,
    })));
  }, [md, hasContent]);

  useEffect(() => {
    if (!hasContent) return;
    const id = headingIdFromHash(location.hash);
    const container = outRef.current;
    if (!container) return;

    let section = '';
    let target: HTMLElement | null = null;
    for (const heading of container.querySelectorAll<HTMLElement>('.helpManual [id]')) {
      if (heading.tagName === 'H2') section = heading.id;
      if (heading.id === id) {
        target = heading;
        break;
      }
    }
    setCurrentSection(target ? section : '');
    if (location.hash && !target) return;

    const top = target
      ? container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 18
      : 0;
    try {
      container.scrollTo({ top, behavior: scrollBehavior() });
    } catch {
      container.scrollTop = top;
    }
    // Leave the section picker focused so its native arrow-key navigation still works.
    if (target && document.activeElement?.id !== 'help-section') {
      target.tabIndex = -1;
      target.focus({ preventScroll: true });
    }
  }, [md, hasContent, location.hash, location.key]);

  const markdownComponents = useMemo<Components>(() => {
    return {
      a: ({ href, children, node: _node, ...props }) => {
        const h = typeof href === 'string' ? href : '';
        if (h.startsWith('#')) {
          return <Link {...props} to={{ pathname: location.pathname, search: location.search, hash: h }} replace>{children}</Link>;
        }
        if (h.startsWith('/') && !h.startsWith('//')) {
          return <Link {...props} to={h}>{children}</Link>;
        }
        const isExternal = /^(https?:)?\/\//i.test(h);
        return (
          <a
            href={href}
            {...props}
            target={isExternal ? '_blank' : undefined}
            rel={isExternal ? 'noopener noreferrer' : undefined}
          >
            {children}
          </a>
        );
      },
    };
  }, [location.pathname, location.search]);

  return (
    <div className="page-events page-help">
      <HeaderBar title="Help" />

      {hasContent && sections.length > 0 ? (
        <nav className="helpToolbar" aria-label="Help sections">
          <label htmlFor="help-section">Jump to section</label>
          <select
            id="help-section"
            value={sections.some((section) => section.id === currentSection) ? currentSection : ''}
            onChange={(event) => {
              if (!event.target.value) return;
              navigate({
                pathname: location.pathname,
                search: location.search,
                hash: `#${encodeURIComponent(event.target.value)}`,
              }, { replace: true });
            }}
          >
            <option value="" disabled>Choose a section</option>
            {sections.map((section) => <option key={section.id} value={section.id}>{section.title}</option>)}
          </select>
        </nav>
      ) : null}

      <div
        className="out helpOut"
        ref={outRef}
        role="region"
        aria-label="TipTune user manual"
        aria-busy={status === 'loading'}
        tabIndex={0}
        onScroll={(event) => setShowBackTop(event.currentTarget.scrollTop > 250)}
      >
        <div className="card">
          {status === 'loading' ? (
            <div className="helpState" role="status">Loading help…</div>
          ) : hasContent ? (
            <div className="markdown helpManual">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeSlug]}
                components={markdownComponents}
              >
                {md}
              </ReactMarkdown>
            </div>
          ) : (
            <div className="helpState" role={status === 'error' ? 'alert' : 'status'}>
              <h2>{status === 'error' ? 'Help could not be loaded' : 'No help content is available'}</h2>
              <p>Try loading the manual again. If this continues, restart TipTune and reopen Help.</p>
              {err ? <p className="muted helpErrorDetail">{err}</p> : null}
              <button type="button" onClick={() => {
                outRef.current?.focus({ preventScroll: true });
                setLoadAttempt((attempt) => attempt + 1);
              }}>Retry</button>
            </div>
          )}
        </div>

        <button
          type="button"
          className={`helpBackTop${showBackTop ? '' : ' helpBackTopHidden'}`}
          onClick={() => {
            const el = outRef.current;
            if (!el) return;
            el.focus({ preventScroll: true });
            navigate({ pathname: location.pathname, search: location.search, hash: '' }, { replace: true });
            try {
              el.scrollTo({ top: 0, behavior: scrollBehavior() });
            } catch {
              el.scrollTop = 0;
            }
          }}
        >
          Back to top
        </button>
      </div>
    </div>
  );
}
