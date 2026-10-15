import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../webui/src/spotifyPlayer.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText
  .replace("'@tauri-apps/api/core'", JSON.stringify(import.meta.resolve('@tauri-apps/api/core')));
const moduleFor = (name) => import(`data:text/javascript;base64,${Buffer.from(javascript + `\n// ${name}`).toString('base64')}`);

afterEach(() => { delete globalThis.window; });

async function commandsFor(name, invoke) {
  globalThis.window = { __TAURI_INTERNALS__: { invoke } };
  return moduleFor(name);
}

test('startup finishes before route cleanup and the latest placement', async () => {
  const calls = [];
  let finishStartup;
  const { playerCommand } = await commandsFor('startup', (command, args) => {
    calls.push({ command, args });
    if (command === 'spotify_player_initialize') return new Promise(resolve => { finishStartup = resolve; });
    return Promise.resolve();
  });
  const startup = playerCommand('spotify_player_initialize');
  const cleanup = playerCommand('spotify_player_hide');
  const placement = playerCommand('spotify_player_show', { x: 25, y: 150, width: 900, height: 600 });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.map(call => call.command), ['spotify_player_initialize']);
  finishStartup();
  await Promise.all([startup, cleanup, placement]);
  assert.deepEqual(calls, [
    { command: 'spotify_player_initialize', args: {} },
    { command: 'spotify_player_hide', args: {} },
    { command: 'spotify_player_show', args: { x: 25, y: 150, width: 900, height: 600 } },
  ]);
});

test('a rejected native command does not block later navigation or reload', async () => {
  const calls = [];
  const { playerCommand } = await commandsFor('recovery', command => {
    calls.push(command);
    return calls.length === 1 ? Promise.reject(new Error('Webview unavailable')) : Promise.resolve();
  });
  const failedPlacement = playerCommand('spotify_player_show');
  const cleanup = playerCommand('spotify_player_hide');
  const reload = playerCommand('spotify_player_reload');
  const placement = playerCommand('spotify_player_show');
  await assert.rejects(failedPlacement, /Webview unavailable/);
  await Promise.all([cleanup, reload, placement]);
  assert.deepEqual(calls, ['spotify_player_show', 'spotify_player_hide', 'spotify_player_reload', 'spotify_player_show']);
});

test('importing the player helper in an ordinary browser does not invoke native commands', async () => {
  delete globalThis.window;
  const module = await moduleFor('browser');
  assert.equal(typeof module.playerCommand, 'function');
  assert.equal(globalThis.window, undefined);
});
