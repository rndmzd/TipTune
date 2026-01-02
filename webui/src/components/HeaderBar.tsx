import { Link } from 'react-router-dom';

import type { ReactNode } from 'react';

export function HeaderBar(props: {
  title: string;
  right?: ReactNode;
}) {
  return (
    <div className="actions" style={{ justifyContent: 'space-between' }}>
      <h1>{props.title}</h1>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>{props.right}</div>
    </div>
  );
}

export function EventsLink() {
  return (
    <div className="muted">
      <Link to="/events">Events</Link>
    </div>
  );
}
