import { Link, useLocation } from 'react-router-dom';

import { useEffect, useState } from 'react';

import { apiJson } from '../api';
import { MiniPlayer } from './PlaybackContext';

import type { ReactNode } from 'react';

type SetupStatusResp = {
  ok: true;
  setup_complete: boolean;
};

export function HeaderBar(props: {
  title: string;
  right?: ReactNode;
  showMiniPlayer?: boolean;
}) {
  const location = useLocation();

  const [setupComplete, setSetupComplete] = useState<boolean | null>(null);

  const isActivePath = (path: string) => location.pathname === path;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<SetupStatusResp>('/api/setup/status');
        if (cancelled) return;
        setSetupComplete(!!data.setup_complete);
      } catch {
        if (cancelled) return;
        setSetupComplete(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const showGatedNav = setupComplete === true;

  const showMiniPlayer = props.showMiniPlayer ?? location.pathname !== '/';

  return (
    <div className="headerBar">
      <div className="headerTitleWrap">
        <h1 className="headerTitle">{props.title}</h1>
        {showMiniPlayer ? <MiniPlayer /> : null}
      </div>
      <div className="headerNav">
        {showGatedNav ? (
          <>
            <Link className={`navBtn${isActivePath('/') ? ' navBtnActive' : ''}`} to="/?dashboard=1">
              Dashboard
            </Link>
            <Link className={`navBtn${isActivePath('/history') ? ' navBtnActive' : ''}`} to="/history">
              History
            </Link>
            <Link className={`navBtn${isActivePath('/stats') ? ' navBtnActive' : ''}`} to="/stats">
              Stats
            </Link>
            <Link className={`navBtn${isActivePath('/events') ? ' navBtnActive' : ''}`} to="/events">
              Events
            </Link>
          </>
        ) : null}

        <Link className={`navBtn${isActivePath('/settings') ? ' navBtnActive' : ''}`} to="/settings?dashboard=1">
          Settings
        </Link>
        <Link className={`navBtn${isActivePath('/setup') ? ' navBtnActive' : ''}`} to="/setup?rerun=1">
          Setup Wizard
        </Link>
        <Link className={`navBtn${isActivePath('/help') ? ' navBtnActive' : ''}`} to="/help">
          Help
        </Link>
        {props.right}
      </div>
    </div>
  );
}

export function EventsLink() {
  const location = useLocation();
  const isActive = location.pathname === '/events';
  return (
    <Link className={`navBtn navBtnGhost${isActive ? ' navBtnActive' : ''}`} to="/events">
      Events
    </Link>
  );
}
