import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser', fullyParallel: false, workers: 1,
  timeout: 30000, use: { baseURL: 'http://127.0.0.1:18765', viewport: { width: 1920, height: 1080 }, browserName: 'chromium' },
  outputDir: 'output/overlay-tests',
  webServer: {
    command: `${process.platform === 'win32' ? 'env\\Scripts\\python.exe' : 'python'} tests/overlay_browser_server.py`,
    url: 'http://127.0.0.1:18765/api/overlay/state', reuseExistingServer: !process.env.CI,
  },
});
