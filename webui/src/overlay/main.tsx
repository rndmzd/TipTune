import { createRoot } from 'react-dom/client';
import { OverlayCanvas } from './OverlayCanvas';
import { useOverlay } from './useOverlay';
import './page.css';

function BrowserOverlay() {
  const { state } = useOverlay();
  return <OverlayCanvas state={state} />;
}
createRoot(document.getElementById('overlay-root')!).render(<BrowserOverlay />);
