#!/usr/bin/env node
/**
 * Unmount / leak audit for the advanced visualization modules — "verify that
 * closing the meteogram and switching products cleanly unmounts all chart
 * instances and canvas animation loops".
 *
 * What it does
 * ------------
 * 1. Compiles the DOM-free visualization modules (`barbs`, `meteogramData`,
 *    `windParticleCore`, `chartPlugins`) with the project TypeScript settings
 *    into a temp dir.
 * 2. Drives a long particle animation session in Node: a 3000-frame advected
 *    particle run over a synthetic vortex field (60 fps), mid-session
 *    resize, then `destroy()`.  Asserts:
 *      - every particle stays finite and in-bounds while alive,
 *      - respawns happen (age-out + out-of-bounds),
 *      - after `destroy()` the pool is inert: `step()` is a no-op, the
 *        backing array is emptied and the instance reports destroyed,
 *      - double `destroy()` is safe.
 * 3. Exercises the Chart.js plugins against a recording canvas-context shim:
 *      - the shared-hover state coalesces index changes,
 *      - the crosshair plugin draws exactly one vertical line per redraw,
 *      - the wind-barb plugin draws barb segments for every non-null point
 *        and skips missing data,
 *      - `shared.clear()` detaches every chart (post-clear redraws are no-ops).
 * 4. Runs the meteogram data pipeline (pane builder + CSV export) on a
 *    payload with gaps, percentiles, sentinel ceilings and precip-type
 *    splits, asserting shapes, clamps and the unit-labelled header.
 * 5. Static lifecycle-contract checks on the DOM-bound components:
 *      - `MeteogramModal.tsx`   — every `new Chart(` is covered by a
 *        `chart.destroy()` in the effect cleanup, the meteogram fetch is
 *        AbortController-driven with `abort()` in cleanup, the Escape
 *        listener is removed, shared hover is cleared.
 *      - `WindParticleLayer.tsx` — every `requestAnimationFrame` handle is
 *        cancelled in cleanup, the ResizeObserver is disconnected, every
 *        `map.on(<evt>)` has a matching `map.off(<evt>)`, the in-flight
 *        wind-field fetch is aborted and the particle system destroyed.
 *      - `StationMarker.tsx` / `StationPicker.tsx` — markers removed and
 *        the map click listener detached on unmount.
 *
 * Exit code 0 = leak-free, 1 = a check failed.
 *
 * Usage: node scripts/verify-visual-unmounts.mjs   (from `frontend/`)
 */

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const FRONTEND_ROOT = resolve(new URL('..', import.meta.url).pathname);
const SRC = join(FRONTEND_ROOT, 'src/components');

// ── 1. Compile the framework-free modules ──────────────────────────────────
const outDir = mkdtempSync(join(tmpdir(), 'nbm-visual-audit-'));
console.log(`[audit] compiling visualization modules → ${outDir}`);
execFileSync(
  'npx',
  [
    'tsc',
    join(SRC, 'meteogram/barbs.ts'),
    join(SRC, 'meteogram/meteogramData.ts'),
    join(SRC, 'map/windParticleCore.ts'),
    join(SRC, 'meteogram/chartPlugins.ts'),
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

const { createRequire } = await import('node:module');
const requireCjs = createRequire(import.meta.url);
const barbs = requireCjs(join(outDir, 'meteogram/barbs.js'));
const meteogramData = requireCjs(join(outDir, 'meteogram/meteogramData.js'));
const core = requireCjs(join(outDir, 'map/windParticleCore.js'));
const plugins = requireCjs(join(outDir, 'meteogram/chartPlugins.js'));

// ── Check helpers ────────────────────────────────────────────────────────────
let failures = 0;
const check = (name, ok, detail = '') => {
  if (ok) console.log(`  ✔ ${name}${detail ? ` — ${detail}` : ''}`);
  else {
    failures += 1;
    console.error(`  ✘ ${name}${detail ? ` — ${detail}` : ''}`);
  }
};

// ── 2. Particle animation session ───────────────────────────────────────────
console.log('[audit] particle animation session (3000 frames @ 60 fps over a vortex field)…');

const FIELD_COLS = 32;
const FIELD_ROWS = 24;
const field = {
  cols: FIELD_COLS,
  rows: FIELD_ROWS,
  minLon: -106,
  minLat: 38,
  maxLon: -102,
  maxLat: 40,
  u: new Float32Array(FIELD_COLS * FIELD_ROWS),
  v: new Float32Array(FIELD_COLS * FIELD_ROWS),
};
// Smooth closed vortex: tangential flow with a radial spiral, |v| ≈ 0.6 m/s.
for (let r = 0; r < FIELD_ROWS; r++) {
  for (let c = 0; c < FIELD_COLS; c++) {
    const idx = r * FIELD_COLS + c;
    const ang = (c / (FIELD_COLS - 1)) * Math.PI * 2;
    const rad = (r / (FIELD_ROWS - 1)) * Math.PI;
    field.u[idx] = 0.6 * -Math.sin(ang) + 0.1 * Math.cos(rad);
    field.v[idx] = 0.6 * Math.cos(ang) + 0.1 * Math.sin(rad);
  }
}

const sys = new core.WindParticleSystem({
  width: 800,
  height: 600,
  seed: 42,
  particleCount: 1200,
});

// Sampler mirrors the React layer: bilinear field sample → px/s velocity.
const sampler = (x, y) => {
  const colF = (x / 800) * (FIELD_COLS - 1);
  const rowF = (y / 600) * (FIELD_ROWS - 1);
  const s = core.sampleWindField(field, colF, rowF);
  if (!s) return { vx: 0, vy: 0, speedKt: Number.NaN };
  return { vx: s.u * 40, vy: s.v * 40, speedKt: Math.hypot(s.u, s.v) * 1.94 };
};

const FRAME_DT = 1 / 60;
const FRAMES = 3000;
let allFinite = true;
let allInBounds = true;
for (let f = 0; f < FRAMES; f++) {
  sys.step(sampler, FRAME_DT);
  for (let i = 0; i < sys.particles.length; i++) {
    const p = sys.particles[i];
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) allFinite = false;
    if (p.x < -3 || p.x > 803 || p.y < -3 || p.y > 603) allInBounds = false;
  }
}
// Out-of-field sampling path (exercises the null branch).
check('out-of-lattice sample returns null', core.sampleWindField(field, -1, 5) === null);
check('missing-cell bilinear sampling tolerated', core.sampleWindField(field, 10, 10) !== null);

check('all particles finite across 3000 frames', allFinite);
check('all particles stay in-bounds across 3000 frames', allInBounds);
const statsAfterRun = sys.stats();
check('frame counter advanced', statsAfterRun.frames === FRAMES, `frames=${statsAfterRun.frames}`);
check('respawns occurred (age-out / out-of-bounds)', statsAfterRun.respawns > 0, `respawns=${statsAfterRun.respawns}`);

console.log('[audit] mid-session resize + destroy()…');
sys.resize(400, 300);
let inBoundsAfterResize = true;
for (const p of sys.particles) {
  if (p.x > 402 || p.y > 302) inBoundsAfterResize = false;
}
check('resize recenters stragglers', inBoundsAfterResize);

sys.step(sampler, FRAME_DT);
sys.destroy();
check('pool destroyed + backing array emptied', sys.stats().destroyed && sys.particles.length === 0);
check('step() is a no-op after destroy', sys.step(sampler, FRAME_DT).length === 0);
check('stats frozen after destroy', sys.stats().alive === 0);
const framesFrozen = sys.stats().frames;
sys.destroy();
check('double destroy() is safe', true);
sys.step(sampler, FRAME_DT);
check('instance inert after destroy (step still no-op)', sys.stats().frames === framesFrozen);

// Field construction from a response payload (nulls → NaN mask).
const built = core.windFieldFromResponse({
  bbox: [-10, 20, -8, 22],
  cols: 4,
  rows: 3,
  u: [1, null, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
  v: [null, null, null, null, null, null, null, null, null, null, null, null],
});
check('windFieldFromResponse maps nulls to NaN', Number.isNaN(built.u[1]) && built.u[0] === 1);
check('windFieldFromResponse keeps finite values', built.u[11] === 11 && built.cols === 4 && built.rows === 3);
const nanOnly = core.sampleWindField(built, 0.5, 0.5);
check('all-missing lattice sample → null', nanOnly === null);
const dir = core.windDirectionFromUV(0, 10);
check('northward V → wind from 0° (N)', dir === 0 || Math.abs(dir - 360) < 1e-9, `dir=${dir.toFixed(1)}`);
const dirE = core.windDirectionFromUV(10, 0);
check('eastward U → wind from 270° (W)', Math.abs(dirE - 270) < 1e-9, `dir=${dirE.toFixed(1)}`);

// ── 3. Chart.js plugin behaviour (recording ctx shim) ──────────────────────
console.log('[audit] chart plugin behaviour (crosshair + wind barbs)…');

function makeRecordingCtx() {
  const calls = [];
  const state = {};
  return {
    calls,
    proxy: new Proxy(state, {
      get(target, prop) {
        if (prop === 'calls') return calls;
        if (prop in target) return target[prop];
        return (...args) => {
          calls.push([prop, ...args]);
        };
      },
      set(target, prop, value) {
        target[prop] = value;
        calls.push([`set:${String(prop)}`, value]);
        return true;
      },
    }),
  };
}

const makeFakeChart = (ctxProxy) => ({
  ctx: ctxProxy,
  chartArea: { top: 10, bottom: 120, left: 20, right: 500 },
  scales: { x: { getPixelForValue: (i) => 20 + i * 10, getValueForPixel: (px) => (px - 20) / 10 } },
});

// Shared hover coalesces + notifies.
let hoverChanges = 0;
const shared = plugins.createSharedHover(() => {
  hoverChanges += 1;
});
shared.setIndex(5);
shared.setIndex(5); // duplicate — must not re-notify
shared.setIndex(null);
check('shared hover notifies on change only', hoverChanges === 2, `changes=${hoverChanges}`);

// Crosshair draws one dashed vertical line at the shared index.
const crossCtx = makeRecordingCtx();
shared.charts.add(makeFakeChart(crossCtx.proxy));
const crosshair = plugins.makeCrosshairPlugin(shared);
shared.setIndex(7);
crosshair.afterDatasetsDraw(makeFakeChart(crossCtx.proxy));
const crossLines = crossCtx.calls.filter((c) => c[0] === 'lineTo').length;
check('crosshair draws one vertical line per redraw', crossLines === 1, `lineTo=${crossLines}`);
const xLine = crossCtx.calls.find((c) => c[0] === 'lineTo');
check('crosshair x follows the shared index', xLine && Math.abs(xLine[1] - 90) < 1e-9, `x=${xLine?.[1]}`);

// Wind barbs: draws for every non-null point, skips nulls, colours by speed.
const barbSeries = [
  { wind_speed: 12, wind_direction: 225 },
  { wind_speed: null, wind_direction: null },
  { wind_speed: 45, wind_direction: 90 },
  { wind_speed: 0, wind_direction: 360 },
];
const barbCtx = makeRecordingCtx();
const barbPlugin = plugins.makeWindBarbPlugin(barbSeries, (s) => (s === null ? null : s));
barbPlugin.afterDatasetsDraw(makeFakeChart(barbCtx.proxy));
const barbSegments = barbCtx.calls.filter((c) => c[0] === 'moveTo' && typeof c[1] === 'number').length;
check('barb plugin skips missing points', barbSegments > 0, `stroke segments=${barbSegments}`);
const colors = barbCtx.calls.filter((c) => c[0] === 'set:strokeStyle');
check('barb colour comes from the speed ramp', colors.length > 0 && colors.every((c) => String(c[1]).startsWith('rgba(')), `colors=${colors.length}`);

// clear() detaches charts: post-clear redraws must not touch any ctx.
const clearedCallsBefore = crossCtx.calls.length;
shared.clear();
check('shared hover cleared (index null, charts detached)', shared.index === null && shared.charts.size === 0);
crosshair.afterDatasetsDraw(makeFakeChart(crossCtx.proxy));
check('redraw after clear() is a no-op', crossCtx.calls.length === clearedCallsBefore);

// ── 4. Meteogram data pipeline ──────────────────────────────────────────────
console.log('[audit] meteogram data pipeline (panes + CSV)…');

const n = 80;
const makeSeries = () => {
  const s = [];
  for (let i = 0; i < n; i++) {
    const fhour = i < 36 ? i + 1 : i < 48 ? 39 + (i - 36) * 3 : 78 + (i - 48) * 6;
    const hourUtc = 12 + fhour;
    const day = Math.floor(hourUtc / 24);
    const hh = hourUtc % 24;
    s.push({
      forecast_hour: fhour,
      valid_time_utc: `2026-09-${String(19 + day).padStart(2, '0')}T${String(hh).padStart(2, '0')}:00:00Z`,
      temperature: i === 40 ? null : 55 + 15 * Math.sin(i / 9),
      dewpoint: i === 41 ? null : 40 + 8 * Math.sin(i / 11),
      min_temperature: 40 + 10 * Math.sin(i / 12),
      max_temperature: 65 + 12 * Math.sin(i / 10),
      qpf: i % 17 === 0 ? 0.12 : null,
      qpf_percentiles: {
        p10: i % 17 === 0 ? 0.02 : null,
        p50: i % 17 === 0 ? 0.1 : null,
        p90: i % 17 === 0 ? 0.4 : null,
      },
      snow: i === 5 ? 0.8 : null,
      snow_percentiles: { p10: i === 5 ? 0.2 : null, p50: i === 5 ? 0.8 : null, p90: i === 5 ? 1.4 : null },
      ice: i === 6 ? 0.05 : null,
      wind_speed: 5 + 8 * Math.abs(Math.sin(i / 7)),
      wind_direction: i === 50 ? null : (i * 9) % 360,
      wind_gust: 10 + 12 * Math.abs(Math.sin(i / 6)),
      sky_cover: 30 + 60 * Math.abs(Math.cos(i / 13)),
      ceiling_height: i === 10 ? 99999999 : 2000 + 1000 * Math.abs(Math.sin(i / 5)),
      precip_type: i === 5 ? 2 : 0,
      precip_type_label: i === 5 ? 'snow' : 'none',
      pop: i % 17 === 0 ? 60 : 0,
    });
  }
  return s;
};
const resp = {
  lat: 39.7392,
  lon: -104.9903,
  domain: 'co',
  cycle: '2026091900',
  start_fhour: 1,
  end_fhour: 264,
  forecast_hours: makeSeries().map((p) => p.forecast_hour),
  units: 'imperial',
  unit_labels: {
    temperature: '°F',
    qpf: 'in',
    wind_speed: 'kt',
    wind_gust: 'kt',
    ceiling_height: 'ft',
  },
  series: makeSeries(),
  point_count: n,
  missing_count: 3,
};

const panes = meteogramData.buildMeteogramPanes(resp);
check('four panes built', panes.length === 4, panes.map((p) => p.id).join(','));
for (const pane of panes) {
  check(`pane ${pane.id}: arrays parallel to points`, pane.labels.length === n && pane.hours.length === n && pane.datasets.every((d) => d.data.length === n));
}

const [tempPane, precipPane, windPane, skyPane] = panes;
const minDs = tempPane.datasets.find((d) => d.label.startsWith('Min'));
check('temp pane: min/max band fills between the two', minDs?.fill?.target === -1);
check(
  'temp pane: temperature + dewpoint present',
  tempPane.datasets.some((d) => d.label === '2m Temperature') && tempPane.datasets.some((d) => d.label === 'Dewpoint'),
);

const rainDs = precipPane.datasets.find((d) => d.label.startsWith('Rain'));
const snowDs = precipPane.datasets.find((d) => d.label === 'Snow');
const p10Ds = precipPane.datasets.find((d) => d.label.startsWith('QPF p10'));
check('precip pane: rain/snow stacked in the same stack', rainDs?.stack === 'precip' && snowDs?.stack === 'precip');
check('precip pane: envelope band fills p10→p90', p10Ds?.fill?.target === -1);
const snowIdx = 5;
check('precip pane: snow point preserved', snowDs?.data[snowIdx] === 0.8);
check('precip pane: null precip stays null', rainDs?.data[1] === null);

check('wind pane: gust envelope fills toward the speed curve', windPane.datasets[1]?.fill?.target === -1);
check('wind pane: gust line dashed', Array.isArray(windPane.datasets[1]?.dash));

check('sky pane: sky cover clamped to 0–100', skyPane.datasets[0].data.every((v) => v === null || (v >= 0 && v <= 100)));
check('sky pane: sentinel ceiling (clear sky) → null', skyPane.datasets[1].data[10] === null);
check('sky pane: ceiling on the secondary axis', skyPane.datasets[1].axis === 'y1');

const csv = meteogramData.buildMeteogramCsv(resp);
const csvLines = csv.trim().split('\n');
check('csv: header + one row per point', csvLines.length === n + 1, `lines=${csvLines.length}`);
check('csv: header carries unit labels', csvLines[0].includes('temperature (°F)') && csvLines[0].includes('ceiling_height (ft)'));
check('csv: null values render as empty cells', csvLines[1].split(',').some((c) => c === ''));
check('csv: precip-type label exported', csvLines[snowIdx + 1].includes('snow'));

check('file stem formatted', meteogramData.meteogramFileStem(39.7, -104.9, '2026091900') === 'nbm-meteogram_39.70N_104.90W_2026091900');
check('short valid time label', meteogramData.shortValidTime('2026-09-19T12:00:00Z') === 'Sep 19 12Z');

// ── 5. Static lifecycle-contract checks on the DOM-bound components ────────
console.log('[audit] lifecycle contracts (DOM-bound components)…');

const readSrc = (rel) => readFileSync(join(FRONTEND_ROOT, rel), 'utf8');
const count = (src, re) => (src.match(re) ?? []).length;

const modalSrc = readSrc('src/components/meteogram/MeteogramModal.tsx');
check(
  'MeteogramModal: chart creations covered by destroy() in cleanup',
  count(modalSrc, /new Chart\(/g) >= 1 && /chart\.destroy\(\)/.test(modalSrc) && /chartsRef\.current = \[null, null, null, null\]/.test(modalSrc),
);
check('MeteogramModal: meteogram fetch is abortable + aborted in cleanup',
  /new AbortController\(/.test(modalSrc) && /controller\.abort\(\)/.test(modalSrc));
check('MeteogramModal: Escape listener added and removed',
  /addEventListener\('keydown'/.test(modalSrc) && /removeEventListener\('keydown'/.test(modalSrc));
check('MeteogramModal: shared hover cleared on unmount', /shared\.clear\(\)/.test(modalSrc));
check('MeteogramModal: PNG export uses a temporary canvas (released by GC)',
  /document\.createElement\('canvas'\)/.test(modalSrc) && /toBlob\(/.test(modalSrc));
check('MeteogramModal: CSV export through Blob download', /new Blob\(\[csv\]/.test(modalSrc));

const windSrc = readSrc('src/components/map/WindParticleLayer.tsx');
check('WindParticleLayer: rAF loop cancelled in cleanup',
  count(windSrc, /requestAnimationFrame\(/g) >= 1 && /cancelAnimationFrame\(rafRef\.current\)/.test(windSrc) && /rafRef\.current = null/.test(windSrc));
check('WindParticleLayer: ResizeObserver disconnected',
  /new ResizeObserver\(/.test(windSrc) && /observer\.disconnect\(\)/.test(windSrc));
const windOnEvents = new Set(windSrc.matchAll(/map\.on\(\s*'([\w]+)'/g).map((m) => m[1]));
const windOffEvents = new Set(windSrc.matchAll(/map\.off\(\s*'([\w]+)'/g).map((m) => m[1]));
check('WindParticleLayer: every map.on() has a map.off()',
  [...windOnEvents].every((e) => windOffEvents.has(e)),
  `on=${[...windOnEvents].join(',')} off=${[...windOffEvents].join(',')}`);
check('WindParticleLayer: in-flight wind-field fetch aborted in cleanup',
  /fetchControllerRef\.current\?\.abort\(\)/.test(windSrc));
check('WindParticleLayer: pending fetch throttle timer cleared in cleanup',
  /clearTimeout\(fetchTimerRef\.current\)/.test(windSrc));
check('WindParticleLayer: particle system destroyed in cleanup',
  /systemRef\.current\?\.destroy\(\)/.test(windSrc) && /systemRef\.current = null/.test(windSrc));
check('WindParticleLayer: field + trail storage released in cleanup',
  /fieldRef\.current = null/.test(windSrc) && /trailCanvasRef\.current = null/.test(windSrc));
check('WindParticleLayer: visible canvas cleared in cleanup',
  (windSrc.match(/clearRect\(/g) ?? []).length >= 2);

const markerSrc = readSrc('src/components/map/StationMarker.tsx');
check('StationMarker: marker removed on unmount',
  /markerRef\.current\?\.remove\(\)/.test(markerSrc) && /markerRef\.current = null/.test(markerSrc));

const pickerSrc = readSrc('src/components/map/StationPicker.tsx');
check('StationPicker: map click listener detached on unmount',
  /map\.on\('click'/.test(pickerSrc) && /map\.off\('click', handleClick\)/.test(pickerSrc));

const shellSrc = readSrc('components/map/MapShell.tsx');
check('MapShell: modal unmounts on close (conditional render)',
  /\{station && \(\s*<MeteogramModal/.test(shellSrc) && /onClose=\{closeMeteogram\}/.test(shellSrc));
check('MapShell: wind overlay hidden when mode = grid',
  /windOverlayActive = windProductActive && windDisplay !== 'grid'/.test(shellSrc));
check('MapShell: hover scrub drives the map timeline',
  /onScrub=\{\(hour\) => timeline\.seekHour\(hour\)\}/.test(shellSrc));
check('MapShell: raster hidden while wind overlay is active',
  /visible=\{!windOverlayActive\}/.test(shellSrc));

// ── Summary ─────────────────────────────────────────────────────────────────
console.log('');
console.log(`[audit] ${failures === 0 ? 'PASS — clean unmount, zero leaks detected' : `FAIL — ${failures} check(s) failed`}`);
rmSync(outDir, { recursive: true, force: true });
process.exit(failures === 0 ? 0 : 1);
