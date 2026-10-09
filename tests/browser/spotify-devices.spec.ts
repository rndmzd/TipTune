import { test, expect } from '@playwright/test';

const clientError = 'Spotify rejected API client authorization (invalid_client). Reconnect Spotify in Settings. If it still fails, check the app and Client ID in your Spotify Developer Dashboard.';
const webDevice = { id: 'web', name: 'Web Player', is_active: true };

async function settingsMocks(page: any) {
  const cfg = { Spotify: { client_id: 'example-client', redirect_url: 'http://127.0.0.1:8888/callback' },
    General: { setup_complete: 'true', request_history_size: '1000' }, Music: { source: 'spotify' }, OBS: { enabled: 'false' } };
  await page.route('**/api/config', (route: any) => route.fulfill({ json: { ok: true, config: cfg } }));
  await page.route('**/api/setup/status', (route: any) => route.fulfill({ json: { ok: true, setup_complete: true } }));
  await page.route('**/api/queue', (route: any) => route.fulfill({ json: { ok: true, queue: { playback_device_id: 'saved' } } }));
  await page.route('**/api/spotify/auth/status', (route: any) => route.fulfill({ json: {
    ok: true, configured: true, authorized: false, client_ready: false, in_progress: false, error: clientError,
  } }));
}

test('legacy device errors are visible and failed device selection is disabled', async ({ page }) => {
  await settingsMocks(page);
  await page.route('**/api/spotify/devices', (route) => route.fulfill({ json: { ok: true, devices: [], error: clientError } }));
  await page.goto('/settings');
  await expect(page.getByRole('alert').filter({ hasText: 'invalid_client' }).first()).toBeVisible();
  await expect(page.getByLabel('Available devices')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Apply + Save', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Connect Spotify', exact: true })).toBeEnabled();
  await expect(page.getByText('Spotify returned no devices.', { exact: false })).toHaveCount(0);
  await page.screenshot({ path: 'output/overlay-qa/spotify-devices-error.png', fullPage: true });
});

test('refresh recovers from API errors and preserves an unsaved device selection', async ({ page }) => {
  await settingsMocks(page);
  let fail = true;
  await page.route('**/api/spotify/devices', (route) => route.fulfill({ json: fail
    ? { ok: false, devices: [], error: clientError }
    : { ok: true, devices: [webDevice, { id: 'saved', name: 'Desktop' }, { id: 'restricted', name: 'Restricted device', is_restricted: true }] } }));
  await page.goto('/settings');
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
  fail = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByLabel('Available devices')).toHaveValue('saved');
  await page.getByLabel('Available devices').selectOption('web');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByLabel('Available devices')).toHaveValue('web');
  await expect(page.getByRole('option', { name: 'Restricted device (restricted)' })).toBeDisabled();
  await page.route('**/api/spotify/device', (route) => route.fulfill({ json: { ok: false, error: 'Device could not be selected.' } }));
  await page.getByRole('button', { name: 'Apply + Save', exact: true }).click();
  await expect(page.getByText('Error: Device could not be selected.')).toBeVisible();
});

test('empty results and backend connection failures have different messages', async ({ page }) => {
  await settingsMocks(page);
  await page.route('**/api/spotify/devices', (route) => route.fulfill({ json: { ok: true, devices: [] } }));
  await page.goto('/settings');
  await expect(page.getByText('Spotify returned no devices.', { exact: false })).toBeVisible();
  await page.route('**/api/spotify/devices', (route) => route.abort('failed'));
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Unable to reach TipTune backend' })).toBeVisible();
  await expect(page.getByText('Spotify returned no devices.', { exact: false })).toHaveCount(0);
});

test('Settings reconnect uses saved credentials, polls completion, and refreshes devices', async ({ page }) => {
  await settingsMocks(page);
  let inProgress = false;
  let complete = false;
  await page.addInitScript(() => { window.open = ((url: string) => { (window as any).authOpened = url; return null; }) as any; });
  await page.route('**/api/spotify/auth/status', (route) => route.fulfill({ json: {
    ok: true, configured: true, authorized: complete, client_ready: complete, in_progress: inProgress && !complete,
    error: complete || inProgress ? null : clientError,
  } }));
  await page.route('**/api/spotify/auth/start', (route) => {
    inProgress = true;
    return route.fulfill({ json: { ok: true, auth_url: 'https://accounts.spotify.com/authorize?client_id=example-client' } });
  });
  await page.route('**/api/spotify/devices', (route) => route.fulfill({ json: complete
    ? { ok: true, devices: [webDevice] } : { ok: false, devices: [], error: clientError } }));
  await page.goto('/settings');
  await page.getByRole('button', { name: 'Connect Spotify', exact: true }).click();
  await expect(page.getByText('Waiting for Spotify authorization…')).toBeVisible();
  expect(await page.evaluate(() => (window as any).authOpened)).toContain('accounts.spotify.com/authorize');
  complete = true;
  await expect(page.getByLabel('Available devices')).toHaveValue('web');
  await expect(page.getByRole('button', { name: 'Reconnect Spotify', exact: true })).toBeEnabled();
  await page.getByLabel('Client ID', { exact: true }).fill('changed-client');
  await expect(page.getByRole('button', { name: 'Reconnect Spotify', exact: true })).toBeDisabled();
  await expect(page.getByText('Save changes before connecting Spotify.')).toBeVisible();
});
