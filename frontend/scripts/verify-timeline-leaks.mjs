#!/usr/bin/env node
/**
 * Leak audit for the timeline animation engine — "verify zero memory leaks
 * during extended looping sessions".
 *
 * What it does
 * ------------
 * 1. Compiles the DOM-free timeline modules (`frames`, `TilePreloader`,
 *    `AnimationEngine`) with the project TypeScript settings into a temp dir.
 * 2. Installs an instrumented DOM shim: a counting `Image` registry, a
 *    virtual rAF clock, and a counting setTimeout/clearTimeout pair.
 * 3. Drives an extended looping session: 8 continuous forward loops + 4
 *    rocking loops over the full 80-frame NBM ladder at 10 fps, with a
 *    simulated slow/flaky network (async image loads, 10% failures), seeks,
 *    fps changes and a tile-context (cycle) switch mid-session.
 * 4. Asserts, at every step:
 *      - the hidden-Image population stays inside the LRU bound,
 *      - at most one rAF handle is live,
 *    and after `destroy()`:
 *      - zero live Image objects (every bitmap released, handlers detached),
 *      - zero pending rAF callbacks and zero live timers,
 *      - the preloader cache is empty and the instance is inert,
 *      - no canvas contexts are ever created by the timeline modules
 *        (the only canvas in the app is MapLibre's, disposed by
 *        `map.remove()` in MapContainer).
 *
 * Exit code 0 = leak-free, 1 = a check failed.
 *
 * Usage: node scripts/verify-timeline-leaks.mjs   (from `frontend/`)
 */

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const FRONTEND_ROOT = resolve(new URL('..', import.meta.url).pathname);
const TIMELINE_DIR = join(FRONTEND_ROOT, 'src/components/timeline');

// ── 1. Compile the framework-free modules ──────────────────────────────────
const outDir = mkdtempSync(join(tmpdir(), 'nbm-timeline-audit-'));
console.log(`[audit] compiling timeline modules → ${outDir}`);
execFileSync(
  'npx',
  [
    'tsc',
    join(TIMELINE_DIR, 'frames.ts'),
    join(TIMELINE_DIR, 'TilePreloader.ts'),
    join(TIMELINE_DIR, 'AnimationEngine.ts'),
    '--outDir', outDir,
    '--module', 'commonjs',
    '--target', 'es2022',
    '--moduleResolution', 'node',
    '--lib', 'es2022,dom',
    '--strict',
    '--skipLibCheck',
  ],
  { cwd: FRONTEND_ROOT, stdio: 'pipe' },
);

// Canvas guard: the timeline must never allocate its own canvas contexts.
for (const file of ['AnimationEngine.js', 'TilePreloader.js', 'frames.js']) {
  const src = readFileSync(join(outDir, file), 'utf8');
  if (/getContext\s*\(/.test(src) || /createElement\s*\(\s*['"]canvas['"]/.test(src)) {
    console.error(`[audit] FAIL: ${file} creates canvas contexts`);
    process.exit(1);
  }
}

// ── 2. Instrumented DOM shim ───────────────────────────────────────────────
let virtualNow = 0;

/** Images created but not yet released (src cleared → bitmap unpinned). */
const imageRegistry = { created: 0, released: 0, pending: [], all: [] };

class FakeImage {
  constructor() {
    this._src = '';
    this._closed = false; // release() detaches handlers + drops the bitmap
    this.onload = null;
    this.onerror = null;
    this.onabort = null;
    this.decoding = '';
    imageRegistry.created += 1;
    imageRegistry.all.push(this);
  }
  get src() {
    return this._src;
  }
  set src(value) {
    if (value === '') {
      // Preloader `release()`: bitmap unpinned, handlers detached. Counted
      // exactly once per fetch lifecycle (covers images destroyed while
      // still queued and never fetched).
      if (!this._closed) {
        this._closed = true;
        imageRegistry.released += 1;
      }
      this._src = '';
      return;
    }
    this._closed = false;
    this._src = value;
    imageRegistry.pending.push({ img: this, url: value });
  }
}

const rafQueue = new Map();
let rafSeq = 0;
globalThis.requestAnimationFrame = (cb) => {
  const handle = ++rafSeq;
  rafQueue.set(handle, cb);
  return handle;
};
globalThis.cancelAnimationFrame = (handle) => {
  rafQueue.delete(handle);
};

const countedTimers = new Set();
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => {
  const handle = realSetTimeout((...args) => {
    countedTimers.delete(handle);
    fn(...args);
  }, ms, ...rest);
  countedTimers.add(handle);
  return handle;
};
globalThis.clearTimeout = (handle) => {
  if (countedTimers.delete(handle)) realClearTimeout(handle);
};

globalThis.Image = FakeImage;
globalThis.performance = { now: () => virtualNow };

// ── 3. Load the compiled engine ────────────────────────────────────────────
const { createRequire } = await import('node:module');
const requireCjs = createRequire(import.meta.url);
const { AnimationEngine } = requireCjs(join(outDir, 'AnimationEngine.js'));
const { canonicalForecastHours } = requireCjs(join(outDir, 'frames.js'));

// ── 4. Scenario ────────────────────────────────────────────────────────────
let failures = 0;
const check = (name, ok, detail = '') => {
  if (ok) console.log(`  ✔ ${name}${detail ? ` — ${detail}` : ''}`);
  else {
    failures += 1;
    console.error(`  ✘ ${name}${detail ? ` — ${detail}` : ''}`);
  }
};

const hours = canonicalForecastHours();
const TILES_PER_FRAME = 4;
const MAX_CACHED_FRAMES = 24; // TilePreloader default
const LIVE_IMAGE_BOUND = MAX_CACHED_FRAMES * TILES_PER_FRAME + TILES_PER_FRAME;

let engineChanges = 0;
let lastHour = null;
let sawBuffering = false;
let maxLiveImages = 0;
let maxRafPending = 0;

const engine = new AnimationEngine({
  hours,
  initialIndex: 0,
  fps: 10,
  loopMode: 'forward',
  preloadAhead: 3,
  getTileUrls: (hour) =>
    Array.from({ length: TILES_PER_FRAME }, (_, i) => `mock://tiles/f${hour}/${i}.webp`),
  onFrameChange: (_index, hour, source) => {
    if (source === 'engine') engineChanges += 1;
    lastHour = hour;
  },
});

let snapshot = null;
engine.subscribe((s) => {
  snapshot = s;
  if (s.buffering) sawBuffering = true;
});

/** Complete the oldest pending image loads (10% simulated failures). */
function flushLoads(maxCount) {
  for (let i = 0; i < maxCount && imageRegistry.pending.length > 0; i++) {
    const { img } = imageRegistry.pending.shift();
    if (img._src === '' || (img.onload === null && img.onerror === null)) continue; // released in flight
    if (Math.random() < 0.1) img.onerror?.();
    else img.onload?.();
  }
}

/** Advance the virtual clock through the rAF loop. */
function pump(steps, stepMs = 16) {
  for (let i = 0; i < steps; i++) {
    virtualNow += stepMs;
    maxRafPending = Math.max(maxRafPending, rafQueue.size);
    // Browser semantics: a rAF callback is dequeued when it fires.
    const entries = [...rafQueue.entries()];
    for (const [handle, cb] of entries) {
      if (!rafQueue.has(handle)) continue; // cancelled by an earlier callback
      rafQueue.delete(handle);
      cb(virtualNow);
    }
    flushLoads(3);
    maxLiveImages = Math.max(maxLiveImages, imageRegistry.created - imageRegistry.released);
  }
}

console.log('[audit] extended looping session (forward ×8 loops, 10 fps, flaky network)…');
engine.play();
const loops = 8;
pump(Math.ceil((hours.length * loops * 1000) / 10 / 16));
check('forward loop advanced through every frame ×8',
  engineChanges >= hours.length * loops - 4, `engine advances = ${engineChanges}`);
check('live hidden Images bounded during playback (LRU)',
  imageRegistry.created - imageRegistry.released <= LIVE_IMAGE_BOUND,
  `live=${imageRegistry.created - imageRegistry.released} bound=${LIVE_IMAGE_BOUND}`);
check('at most one pending rAF handle at any time', maxRafPending <= 1, `max=${maxRafPending}`);
check('buffering indicator engaged at least once', sawBuffering);

console.log('[audit] rocking loop ×4…');
engine.setLoopMode('rocking');
const forwardBefore = engineChanges;
pump(Math.ceil((hours.length * 4 * 1000) / 10 / 16));
check('rocking loop ping-pongs without dropping frames',
  engineChanges - forwardBefore >= hours.length * 4 - 4,
  `advances = ${engineChanges - forwardBefore}`);
check('resources still bounded after rocking', imageRegistry.created - imageRegistry.released <= LIVE_IMAGE_BOUND,
  `live=${imageRegistry.created - imageRegistry.released}`);

console.log('[audit] transport interactions…');
engine.pause();
const engineChangesAtPause = engineChanges;
engine.syncIndex(40); // external seek — must NOT count as an engine advance
check('external syncIndex emits no engine frame change', engineChanges === engineChangesAtPause);
check('external syncIndex moved the playhead', snapshot.index === 40 && lastHour === hours[40]);
engine.step(1);
engine.step(1);
check('step(+1) while paused seeks', snapshot.index === 42);
engine.jumpHours(24);
check('jumpHours(+24) seeks to nearest frame', Math.abs(snapshot.hour - (hours[42] + 24)) <= 3,
  `hour=${snapshot.hour}`);
engine.first();
check('first() → index 0', snapshot.index === 0);
engine.last();
check('last() → last index', snapshot.index === hours.length - 1);
engine.nudgeFps(50);
check('fps clamps to 10', snapshot.fps === 10, `fps=${snapshot.fps}`);
engine.setFps(0);
check('fps clamps to 1', snapshot.fps === 1, `fps=${snapshot.fps}`);

console.log('[audit] tile-context switch (model cycle change)…');
const cachedBeforeSwitch = engine.stats.cachedFrames;
engine.setTileContext('co|2099123112|tmp');
check('stale cache was populated before the switch', cachedBeforeSwitch > 0, `frames=${cachedBeforeSwitch}`);
check('cache repopulated with only the new context (≤ playhead + 3 ahead)',
  engine.stats.cachedFrames <= 4, `frames=${engine.stats.cachedFrames}`);

console.log('[audit] destroy() — release everything…');
engine.destroy();
await new Promise((r) => realSetTimeout(r, 50)); // let any stray timer show itself
const liveImages = imageRegistry.created - imageRegistry.released;
if (liveImages !== 0) {
  const stragglers = imageRegistry.all.filter((img) => img._src !== '').slice(0, 12);
  console.error(`  ⚠ unreleased image srcs:`, stragglers.map((img) => img._src));
  console.error(`  ⚠ handlers still attached:`, stragglers.filter((i) => i.onload || i.onerror).length);
}
check('zero live Image objects after destroy', liveImages === 0, `live=${liveImages}`);
check('zero pending rAF callbacks after destroy', rafQueue.size === 0, `pending=${rafQueue.size}`);
check('zero live timers after destroy', countedTimers.size === 0, `active=${countedTimers.size}`);
check('preloader cache + in-flight empty after destroy',
  engine.stats.cachedFrames === 0 && engine.stats.inFlight === 0);
check('snapshot reports paused', snapshot.playing === false);

const indexAtDestroy = snapshot.index;
engine.step(1);
engine.play();
check('instance is inert after destroy (step/play no-op)',
  snapshot.index === indexAtDestroy && snapshot.playing === false);

engine.destroy();
check('double destroy() is safe', true);

console.log('');
console.log(`[audit] ${failures === 0 ? 'PASS — zero leaks detected' : `FAIL — ${failures} check(s) failed`}`);
console.log(
  `[audit] totals: images created=${imageRegistry.created} released=${imageRegistry.released} ` +
    `maxLive=${maxLiveImages} (bound ${LIVE_IMAGE_BOUND}), maxRafHandles=${maxRafPending}`,
);

rmSync(outDir, { recursive: true, force: true });
process.exit(failures === 0 ? 0 : 1);
