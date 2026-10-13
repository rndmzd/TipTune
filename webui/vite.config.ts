import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { sentryVitePlugin } from '@sentry/vite-plugin';

export default defineConfig({
  build: {
    sourcemap: 'hidden',
    rollupOptions: { input: { app: 'webui/index.html', overlay: 'webui/overlay.html' } },
  },
  plugins: [react(), sentryVitePlugin({
    telemetry: false,
    // Stamp matching debug IDs locally. Upload only the final packaged build in CI.
    sourcemaps: { disable: 'disable-upload' },
    release: { name: '', inject: false, create: false, finalize: false, setCommits: false },
  })],
  define: {
    __APP_VERSION__: JSON.stringify((globalThis as any)?.process?.env?.npm_package_version || ''),
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
});
