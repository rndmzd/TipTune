(() => {
  if (window.__tiptuneAudioOutput) return;
  const deviceLabel = 'CABLE Input (VB-Audio Virtual Cable)';
  const elements = new Set();
  const contexts = new Set();
  const seen = new WeakSet();
  const pauseVersion = new WeakMap();
  let destination = Promise.resolve('');
  let enabled = false;
  let error = '';

  function remember(collection, object) {
    if (!seen.has(object)) { seen.add(object); collection.add(new WeakRef(object)); }
    return object;
  }
  function living(collection) {
    const objects = [];
    for (const reference of collection) {
      const object = reference.deref();
      if (object) objects.push(object); else collection.delete(reference);
    }
    return objects;
  }
  async function routeElement(element) {
    remember(elements, element);
    const id = await destination;
    if (element.sinkId === id) return;
    if (typeof element.setSinkId !== 'function') {
      if (id) throw new Error('This player cannot select an audio output.');
      return;
    }
    await element.setSinkId(id);
  }
  async function routeContext(context) {
    remember(contexts, context);
    if (context.state === 'closed') return;
    const id = await destination;
    if (context.sinkId === id) return;
    if (typeof context.setSinkId !== 'function') {
      if (id) throw new Error('This player cannot select an audio output.');
      return;
    }
    await context.setSinkId(id);
  }

  const play = HTMLMediaElement.prototype.play;
  const pause = HTMLMediaElement.prototype.pause;
  HTMLMediaElement.prototype.pause = function(...args) {
    pauseVersion.set(this, (pauseVersion.get(this) || 0) + 1);
    return pause.apply(this, args);
  };
  HTMLMediaElement.prototype.play = function(...args) {
    const version = pauseVersion.get(this) || 0;
    return routeElement(this).then(() => {
      if ((pauseVersion.get(this) || 0) !== version) throw new DOMException('Playback was interrupted.', 'AbortError');
      return play.apply(this, args);
    });
  };
  const AudioConstructor = window.Audio;
  window.Audio = new Proxy(AudioConstructor, {
    construct(target, args, newTarget) { return remember(elements, Reflect.construct(target, args, newTarget)); },
    apply(target, receiver, args) { return remember(elements, Reflect.apply(target, receiver, args)); },
  });
  const ContextConstructor = window.AudioContext;
  const resume = ContextConstructor.prototype.resume;
  for (const method of ['suspend', 'close']) {
    const original = ContextConstructor.prototype[method];
    ContextConstructor.prototype[method] = function(...args) {
      pauseVersion.set(this, (pauseVersion.get(this) || 0) + 1);
      return original.apply(this, args);
    };
  }
  ContextConstructor.prototype.resume = function(...args) {
    const version = pauseVersion.get(this) || 0;
    return routeContext(this).then(() => {
      if ((pauseVersion.get(this) || 0) !== version) throw new DOMException('Playback was interrupted.', 'AbortError');
      return resume.apply(this, args);
    });
  };
  window.AudioContext = new Proxy(ContextConstructor, {
    construct(target, args, newTarget) {
      const context = remember(contexts, Reflect.construct(target, args, newTarget));
      routeContext(context).catch(exception => { error = exception.message; });
      return context;
    },
  });
  if (window.webkitAudioContext === ContextConstructor) window.webkitAudioContext = window.AudioContext;

  window.__tiptuneAudioOutput = {
    async configure(useCable) {
      enabled = useCable;
      destination = useCable ? navigator.mediaDevices.enumerateDevices().then(devices => {
        const device = devices.find(item => item.kind === 'audiooutput' && item.label === deviceLabel);
        if (!device?.deviceId) throw new Error('VB-CABLE is unavailable. Enable CABLE Input in Windows sound settings.');
        return device.deviceId;
      }) : Promise.resolve('');
      // Also include the app's audio element if routing was first enabled after it mounted.
      for (const element of document.querySelectorAll('audio,video')) remember(elements, element);
      try {
        await destination;
        await Promise.all([...living(contexts).map(routeContext), ...living(elements).map(routeElement)]);
        error = '';
        return { enabled, device: useCable ? deviceLabel : '', error: '' };
      } catch (exception) {
        error = exception.message;
        throw exception;
      }
    },
    status() { return { enabled, error }; },
  };
})();
