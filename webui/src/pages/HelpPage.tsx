import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSlug from 'rehype-slug';

import type { ComponentPropsWithoutRef } from 'react';

import { apiJson } from '../api';
import { HeaderBar } from '../components/HeaderBar';

type UserManualResp = { ok: true; markdown: string };

export function HelpPage() {
  const location = useLocation();
  const outRef = useRef<HTMLDivElement | null>(null);

  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [err, setErr] = useState<string>('');
  const [md, setMd] = useState<string>('');
  const [showBackTop, setShowBackTop] = useState<boolean>(false);

  async function load() {
    setStatus((prev) => (prev === 'ok' ? 'loading' : prev));
    setErr('');

    try {
      const data = await apiJson<UserManualResp>('/api/help/user-manual');
      setMd(String(data.markdown ?? ''));
      setStatus('ok');
    } catch (e: any) {
      setStatus('error');
      setErr(e?.message ? String(e.message) : String(e));
      setMd('');
    }
  }

  useEffect(() => {
    load().catch(() => {});
  }, []);

  useEffect(() => {
    if (!md) return;
    const hash = String(location.hash || '');
    if (!hash || hash === '#') return;

    const id = decodeURIComponent(hash.slice(1));
    if (!id) return;

    const el = document.getElementById(id);
    if (!el) return;

    try {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch {
      el.scrollIntoView();
    }
  }, [md, location.hash]);

  useEffect(() => {
    const el = outRef.current;
    if (!el) return;

    const onScroll = () => {
      const next = el.scrollTop > 250;
      setShowBackTop((prev) => (prev === next ? prev : next));
    };

    onScroll();
    el.addEventListener('scroll', onScroll);
    return () => {
      el.removeEventListener('scroll', onScroll);
    };
  }, [md]);

  const markdownComponents = useMemo(() => {
    return {
      a: ({ href, children, ...props }: ComponentPropsWithoutRef<'a'>) => {
        const h = typeof href === 'string' ? href : '';
        const isHash = h.startsWith('#');
        return (
          <a
            href={href}
            {...props}
            onClick={(e) => {
              if (!isHash) return;
              e.preventDefault();
              const id = decodeURIComponent(h.slice(1));
              if (!id) return;
              try {
                window.history.replaceState(null, '', `${location.pathname}${h}`);
              } catch {
              }
              const el = document.getElementById(id);
              if (!el) return;
              try {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
              } catch {
                el.scrollIntoView();
              }
            }}
          >
            {children}
          </a>
        );
      },
    } satisfies Components;
  }, [location.pathname]);

  return (
    <div className="page-events">
      <HeaderBar title="Help" />

      <div className="row" style={{ marginTop: 12 }}>
        {status === 'loading' ? <span className="muted">Loading…</span> : null}
        {status === 'error' && err ? <span className="muted">Error: {err}</span> : null}
      </div>

      <div className="out helpOut" ref={outRef}>
        <div className="card">
          {md ? (
            <div className="markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeSlug]}
                components={markdownComponents}
              >
                {md}
              </ReactMarkdown>
            </div>
          ) : (
            <div className="muted">(empty)</div>
          )}
        </div>

        <button
          type="button"
          className={`helpBackTop${showBackTop ? '' : ' helpBackTopHidden'}`}
          onClick={() => {
            const el = outRef.current;
            if (!el) return;
            try {
              window.history.replaceState(null, '', location.pathname);
            } catch {
            }
            try {
              el.scrollTo({ top: 0, behavior: 'smooth' });
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
