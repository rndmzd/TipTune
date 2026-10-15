import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const script = await readFile(new URL('../src-tauri/src/audio_output.js', import.meta.url), 'utf8');
const cable = { kind: 'audiooutput', label: 'CABLE Input (VB-Audio Virtual Cable)', deviceId: 'cable' };

function player(devices = [cable]) {
  const events = [];
  const attached = [];
  class Media {
    sinkId = ''; paused = true;
    async setSinkId(id) { events.push(['media sink', id]); this.sinkId = id; }
    async play() { events.push(['play', this.sinkId]); this.paused = false; }
    pause() { this.paused = true; }
  }
  class Context {
    sinkId = ''; state = 'suspended';
    async setSinkId(id) { events.push(['context sink', id]); this.sinkId = id; }
    async resume() { events.push(['resume', this.sinkId]); this.state = 'running'; }
    async suspend() { this.state = 'suspended'; }
    async close() { this.state = 'closed'; }
  }
  const window = { Audio: Media, AudioContext: Context, webkitAudioContext: Context };
  vm.runInNewContext(script, { window, HTMLMediaElement: Media, WeakRef, WeakSet, WeakMap, Set, Proxy, DOMException,
    navigator: { mediaDevices: { enumerateDevices: async () => devices } },
    document: { querySelectorAll: () => attached } });
  return { window, events, attached, Media, output: window.__tiptuneAudioOutput };
}

test('routes detached media and Web Audio to the dedicated cable before playback starts', async () => {
  const { window, events, output } = player();
  await output.configure(true);
  const audio = new window.Audio();
  const context = new window.AudioContext();
  await audio.play();
  await context.resume();
  assert.deepEqual(events.filter(event => ['play', 'resume'].includes(event[0])), [['play', 'cable'], ['resume', 'cable']]);
  assert.equal(window.webkitAudioContext, window.AudioContext);
});

test('switches existing players and contexts back to normal output without changing pause intent', async () => {
  const { window, events, output } = player();
  const paused = new window.Audio();
  const playing = new window.Audio();
  const context = new window.AudioContext();
  await playing.play();
  await context.resume();
  await output.configure(true);
  assert.equal(paused.paused, true);
  assert.equal(playing.paused, false);
  assert.equal(context.state, 'running');
  assert.equal(paused.sinkId, 'cable');
  events.length = 0;
  await output.configure(false);
  assert.equal(playing.sinkId, '');
  assert.equal(context.sinkId, '');
  assert.ok(events.every(event => event[0].includes('sink')));
  assert.equal(paused.paused, true);
});

test('includes DOM media mounted before routing and skips closed contexts', async () => {
  const { window, attached, Media, output } = player();
  const media = new Media();
  attached.push(media);
  const context = new window.AudioContext();
  await Promise.resolve();
  context.state = 'closed';
  context.setSinkId = () => { throw new Error('Closed context must not be routed'); };
  await output.configure(true);
  assert.equal(media.sinkId, 'cable');
});

test('does not substitute a different virtual cable or request microphone access', async () => {
  const { window, events, output } = player([{ ...cable, label: 'CABLE In 16ch (VB-Audio Virtual Cable)' }]);
  await assert.rejects(output.configure(true), /VB-CABLE is unavailable/);
  await assert.rejects(new window.Audio().play(), /VB-CABLE is unavailable/);
  assert.ok(!events.some(event => event[0] === 'play'));
  await output.configure(false);
  await new window.Audio().play();
  assert.deepEqual(events.at(-1), ['play', '']);
});

test('reports sink selection failure and permits a normal-output retry', async () => {
  const { window, output } = player();
  const media = new window.Audio();
  media.setSinkId = async id => { if (id) throw new Error('Device disconnected'); media.sinkId = id; };
  await assert.rejects(output.configure(true), /Device disconnected/);
  assert.equal(output.status().error, 'Device disconnected');
  await output.configure(false);
  assert.equal(output.status().error, '');
  await media.play();
  assert.equal(media.paused, false);
});

test('a manual pause cancels a play waiting for output selection', async () => {
  const { window, events, output } = player();
  await output.configure(true);
  const media = new window.Audio();
  let finishRouting;
  let started;
  const routingStarted = new Promise(resolve => { started = resolve; });
  media.setSinkId = () => new Promise(resolve => { finishRouting = resolve; started(); });
  const playing = media.play();
  await routingStarted;
  media.pause();
  finishRouting();
  await assert.rejects(playing, { name: 'AbortError' });
  assert.equal(media.paused, true);
  assert.ok(!events.some(event => event[0] === 'play'));
});

test('a manual suspend cancels a context resume waiting for output selection', async () => {
  const { window, events, output } = player();
  await output.configure(true);
  const context = new window.AudioContext();
  // Let the constructor's initial routing complete before simulating a slower retry.
  await new Promise(resolve => setImmediate(resolve));
  context.sinkId = '';
  let finishRouting;
  let started;
  const routingStarted = new Promise(resolve => { started = resolve; });
  context.setSinkId = () => new Promise(resolve => { finishRouting = resolve; started(); });
  const playing = context.resume();
  await routingStarted;
  await context.suspend();
  finishRouting();
  await assert.rejects(playing, { name: 'AbortError' });
  assert.equal(context.state, 'suspended');
  assert.ok(!events.some(event => event[0] === 'resume'));
});
