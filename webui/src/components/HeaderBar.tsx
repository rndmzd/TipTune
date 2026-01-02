import { Link, useLocation } from 'react-router-dom';

import type { ReactNode } from 'react';

export function HeaderBar(props: {
  title: string;
  right?: ReactNode;
}) {
  const location = useLocation();

  const isActivePath = (path: string) => location.pathname === path;

  return (
    <div className="headerBar">
      <h1 className="headerTitle">{props.title}</h1>
      <div className="headerNav">
        <Link className={`navBtn${isActivePath('/') ? ' navBtnActive' : ''}`} to="/?dashboard=1">
          Dashboard
        </Link>
        <Link className={`navBtn${isActivePath('/settings') ? ' navBtnActive' : ''}`} to="/settings?dashboard=1">
          Settings
        </Link>
        <Link className={`navBtn${isActivePath('/events') ? ' navBtnActive' : ''}`} to="/events">
          Events
        </Link>
        <Link className={`navBtn${isActivePath('/setup') ? ' navBtnActive' : ''}`} to="/setup?rerun=1">
          Setup Wizard
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
