import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify((globalThis as any)?.process?.env?.npm_package_version || ''),
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
});
