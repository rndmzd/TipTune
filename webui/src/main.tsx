import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import './app.css';
import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { installErrorReporting } from './errorReporting';

void installErrorReporting('webui').then(() => ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary><BrowserRouter>
      <App />
    </BrowserRouter></ErrorBoundary>
  </React.StrictMode>,
));
