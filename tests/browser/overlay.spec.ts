import { test, expect } from '@playwright/test';

test.beforeEach(async ({ request }) => { await request.post('/__test/state', { data: {} }); });

test('standalone overlay is transparent, live, and never initializes audio', async ({ page, request }) => {
  const writes: string[] = [];
  page.on('request', (r) => { if (r.method() !== 'GET') writes.push(r.url()); });
  await page.goto('/overlay');
  await expect(page.getByText('Midnight City')).toBeVisible();
  await expect(page.getByText('StarGazer')).toBeVisible();
  await expect(page.getByText('Moonwalker')).toBeVisible();
  expect(await page.locator('audio,video').count()).toBe(0);
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
  await request.post('/__test/state', { data: { now_playing: null, upcoming: [] } });
  await expect(page.locator('.tt-now,.tt-queue,.tt-alert')).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('alerts coexist with track panels and expire independently', async ({ page, request }) => {
  await page.goto('/overlay');
  await request.post('/api/obs/test_overlay', { data: { overlay: 'WarningOverlay' } });
  await expect(page.getByText('This is a test warning.')).toBeVisible();
  await expect(page.getByText('Midnight City')).toBeVisible();
  await expect(page.locator('.tt-alert')).toHaveCount(0, { timeout: 12000 });
  await expect(page.getByText('Midnight City')).toBeVisible();
});

test('every preset and anchor fits landscape and portrait viewports', async ({ page, request }) => {
  await page.goto('/overlay');
  for (const viewport of [{ width: 1280, height: 720 }, { width: 2560, height: 1440 }, { width: 1080, height: 1920 }]) {
    await page.setViewportSize(viewport);
    for (const layout of ['full', 'compact']) for (const position of ['bottom-left', 'bottom-right', 'top-left', 'top-right']) {
      await request.post('/api/config', { data: { Overlay: { layout, position, scale: '150', queue_length: '5' } } });
      await expect(page.locator('.tt-overlay')).toHaveClass(new RegExp(`tt-${layout} tt-${position}`));
      const bounds = await page.locator('.tt-group').boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
    }
  }
});

test('long Unicode text and failed artwork stay bounded', async ({ page, request }) => {
  await request.post('/__test/state', { data: { now_playing: {
    source: 'youtube', name: '夜空に輝く長い曲名 '.repeat(30), artists: ['Björk'], requester: '星を見る人'.repeat(40),
    album_image_url: 'https://example.invalid/art.jpg',
  } } });
  await page.goto('/overlay');
  await expect(page.locator('.tt-artwork svg')).toBeVisible();
  await expect(page.getByText('Björk')).toBeVisible();
  const bounds = await page.locator('.tt-group').boundingBox();
  expect(bounds!.height).toBeLessThan(1080);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1920);
});

test('draft previews are isolated and save persists after reload', async ({ page, request }) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Stream information display' })).toBeVisible();
  await page.getByLabel('Layout', { exact: true }).selectOption('compact');
  await page.getByRole('button', { name: 'Preview request', exact: true }).click();
  await expect(page.locator('.overlay-preview .tt-alert')).toHaveCount(1);
  const before = await (await request.get('/api/overlay/state')).json();
  expect(before.state.config.layout).toBe('full'); expect(before.state.alerts).toEqual([]);
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('Saved.', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Layout', { exact: true })).toHaveValue('compact');
  await page.getByLabel('Scale (%)', { exact: true }).fill('151');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText(/must be between 50 and 150/)).toBeVisible();
  const after = await (await request.get('/api/overlay/state')).json();
  expect(after.state.config.scale).toBe('100');
});

test('visibility settings hide panels and mobile settings have no overflow', async ({ page, request }) => {
  await request.post('/api/config', { data: { Overlay: { show_now_playing: 'false', show_queue: 'false', show_alerts: 'false' } } });
  await page.goto('/overlay');
  await expect(page.locator('.tt-now,.tt-queue,.tt-alert')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/settings');
  await expect(page.getByLabel('Layout', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('disconnect clears alerts, hides stale data, and reconnects', async ({ page }) => {
  await page.addInitScript(() => {
    class FakeEvents extends EventTarget {
      onerror: (() => void) | null = null;
      constructor() { super(); (window as any).overlayEvents = this; }
      close() {}
    }
    (window as any).EventSource = FakeEvents;
  });
  await page.goto('/overlay');
  const emit = async (revision: number, alerts: boolean) => page.evaluate(({ revision, alerts }) => {
    const state = { schema_version: 1, session_id: 'test', revision, server_timestamp: Date.now() / 1000,
      config: { mode: 'browser' }, now_playing: { name: 'Recovered song', source: 'youtube' }, upcoming: [], total_queue_count: 0,
      status: {}, alerts: alerts ? [{ id: 'alert', kind: 'general', message: 'Temporary', expires_at: Date.now() / 1000 + 30 }] : [] };
    (window as any).overlayEvents.dispatchEvent(new MessageEvent('snapshot', { data: JSON.stringify(state) }));
  }, { revision, alerts });
  await emit(1, true);
  await expect(page.getByText('Temporary')).toBeVisible();
  await page.evaluate(() => (window as any).overlayEvents.onerror());
  await expect(page.locator('.tt-alert')).toHaveCount(0);
  await expect(page.getByText('Recovered song')).toBeVisible();
  await expect(page.locator('.tt-now')).toHaveCount(0, { timeout: 17000 });
  await emit(2, false);
  await expect(page.getByText('Recovered song')).toBeVisible();
});

test('mobile panel inspection stays readable without changing broadcast scale', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/settings');
  await page.getByLabel('Preview framing', { exact: true }).selectOption('inspect');
  await expect(page.locator('.overlay-preview .tt-title').first()).toHaveCSS('font-size', '30px');
  await expect(page.locator('.overlay-preview .tt-group')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  const state = await (await request.get('/api/overlay/state')).json();
  expect(state.state.config.scale).toBe('100');
});
