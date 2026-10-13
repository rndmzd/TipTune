import { createRoot } from 'react-dom/client';
import { OverlayCanvas } from './OverlayCanvas';
import { useOverlay } from './useOverlay';
import './page.css';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { installErrorReporting } from '../errorReporting';

function BrowserOverlay() {
  const { state } = useOverlay();
  return <OverlayCanvas state={state} />;
}
void installErrorReporting('overlay').then(() => createRoot(document.getElementById('overlay-root')!).render(<ErrorBoundary overlay><BrowserOverlay /></ErrorBoundary>));
