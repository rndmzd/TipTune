import type { QueueItem } from '../types';

function parseSpotifyTrackId(v: unknown): string | null {
  try {
    const s = String(v ?? '');
    let m = s.match(/^spotify:track:([a-zA-Z0-9]+)$/);
    if (m) return m[1] ?? null;
    m = s.match(/open\.spotify\.com\/track\/([a-zA-Z0-9]+)/);
    if (m) return m[1] ?? null;
    return null;
  } catch {
    return null;
  }
}

function toDurationLabel(ms: unknown): string | null {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return null;
  const s = Math.floor(n / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

function TrashIcon(props: { size?: number }) {
  const size = typeof props.size === 'number' && Number.isFinite(props.size) && props.size > 0 ? props.size : 16;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function GripIcon(props: { size?: number }) {
  const size = typeof props.size === 'number' && Number.isFinite(props.size) && props.size > 0 ? props.size : 16;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="9" cy="5" r="1.5" />
      <circle cx="15" cy="5" r="1.5" />
      <circle cx="9" cy="12" r="1.5" />
      <circle cx="15" cy="12" r="1.5" />
      <circle cx="9" cy="19" r="1.5" />
      <circle cx="15" cy="19" r="1.5" />
    </svg>
  );
}

export function QueueCard(props: {
  item: QueueItem | string;
  indexLabel: string;
  allowDelete: boolean;
  onDelete?: () => void;
  extraClass?: string;
  rightActions?: React.ReactNode;
  showDragHandle?: boolean;
  onDragHandlePointerDown?: (e: React.PointerEvent<HTMLSpanElement>) => void;
}) {
  const isObj = props.item && typeof props.item === 'object';
  const obj = isObj ? (props.item as QueueItem) : null;
  const uri = isObj ? String(obj?.uri ?? '') : String(props.item ?? '');
  const trackId = isObj ? (obj?.track_id ? String(obj.track_id) : null) : parseSpotifyTrackId(uri);
  const name = isObj && typeof obj?.name === 'string' ? obj.name : null;
  const artists = isObj && Array.isArray(obj?.artists) ? obj!.artists!.filter((x) => typeof x === 'string' && x.trim() !== '') : [];
  const album = isObj && typeof obj?.album === 'string' && obj.album.trim() !== '' ? obj.album : null;
  const duration = isObj ? toDurationLabel(obj?.duration_ms) : null;
  const explicit = isObj ? !!obj?.explicit : false;
  const spotifyUrl =
    isObj && typeof obj?.spotify_url === 'string' && obj.spotify_url.trim() !== ''
      ? obj.spotify_url
      : trackId
        ? `https://open.spotify.com/track/${trackId}`
        : null;
  const artUrl = isObj && typeof obj?.album_image_url === 'string' && obj.album_image_url.trim() !== '' ? obj.album_image_url : null;

  const label = name ? name : trackId ? `spotify:track:${trackId}` : uri || '(unknown)';

  return (
    <div className={props.extraClass ? `queueCard ${props.extraClass}` : 'queueCard'}>
      <div className="queueCardHeader">
        <div className="queueCardHeaderLeft">
          {props.showDragHandle ? (
            <span
              className="queueDragHandle"
              title="Drag to reorder"
              onPointerDown={(e) => {
                props.onDragHandlePointerDown?.(e);
              }}
            >
              <GripIcon size={16} />
            </span>
          ) : null}
          <div className="queueCardTitle">{props.indexLabel}</div>
        </div>
        <div className="queueCardMeta">
          {props.allowDelete ? (
            <button
              className="queueIconBtn"
              type="button"
              title="Remove from queue"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                props.onDelete?.();
              }}
            >
              <TrashIcon size={16} />
            </button>
          ) : null}

          {spotifyUrl ? (
            <a href={spotifyUrl} target="_blank" rel="noreferrer noopener">
              Open in Spotify
            </a>
          ) : null}

          {props.rightActions}
        </div>
      </div>

      <div className="queueCardBody">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {artUrl ? <img className="queueArt" src={artUrl} alt="" /> : null}
          <div style={{ flex: 1 }}>
            <div className="queueTitleMain">{label}</div>
            {artists.length || album ? (
              <div className="queueTitleSub">{[artists.length ? artists.join(', ') : null, album].filter(Boolean).join(' · ')}</div>
            ) : null}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {duration ? <span className="pill pillNeutral">{duration}</span> : null}
          {explicit ? <span className="pill pillWarn">Explicit</span> : null}
        </div>

        <div className="queueMessage">{uri}</div>

        <details>
          <summary>Details</summary>
          <pre>{isObj ? JSON.stringify(props.item, null, 2) : uri}</pre>
        </details>
      </div>
    </div>
  );
}
