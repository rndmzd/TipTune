import { Component, type ErrorInfo, type ReactNode } from 'react';
import { reportFrontendError } from '../errorReporting';

export class ErrorBoundary extends Component<{ children: ReactNode; overlay?: boolean }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    reportFrontendError(error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.props.overlay) return null;
    return (
      <main className="container" role="alert">
        <h1>TipTune ran into a problem</h1>
        <p>Reload the app to try again.</p>
        <button onClick={() => window.location.reload()}>Reload TipTune</button>
      </main>
    );
  }
}
