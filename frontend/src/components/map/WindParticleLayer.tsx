'use client';

/**
 * Animated wind overlay for the 10 m wind field — "Wind Particles" or
 * "Wind Barbs" in the layer menu (vs. the standard raster grid).
 *
 * Data: the U/V wind vector components for the *current viewport* come from
 * ``GET /api/v1/probe/wind-field`` (metric m/s, equirectangular lattice over
 * the map bounds, re-fetched on pan/zoom and forecast-hour changes).
 *
 * Rendering (HTML5 Canvas 2D, chosen over WebGL for zero-dependency trails
 * and trivial teardown):
 *   - **particles** — an advected-particle pool (``WindParticleSystem``).
 *     Each frame, particles are advanced along the bilinearly sampled U/V
 *     field; the on-screen velocity is proportional to wind speed (with a
 *     fixed time-compression so motion reads naturally at any zoom), and the
 *     segment colour follows the speed ramp.  Trails live on a separate
 *     offscreen canvas faded with ``destination-in`` so they dissolve to
 *     transparent over the live map.
 *   - **barbs** — static meteorological barbs at lattice nodes, redrawn on
 *     data/resize (no animation loop while in barbs mode).
 *
 * Unmount contract (verified by ``scripts/verify-visual-unmounts.mjs``):
 *   - the rAF loop is cancelled and the particle system destroyed
 *     (backing arrays emptied),
 *   - the ResizeObserver is disconnected,
 *   - every map listener is removed and the in-flight fetch aborted,
 *   - both canvases are cleared and released.
 */

import { useEffect, useRef, useState } from 'react';

import type { Map as MapLibreMap } from 'maplibre-gl';

import { api } from '@/lib/api';
import type { NbmDomain } from '@/lib/types';

import { windSpeedColor, windSpeedGradient, drawWindBarb } from '@/src/components/meteogram/barbs';

import {
  sampleWindField,
  windDirectionFromUV,
  WindParticleSystem,
  windFieldFromResponse,
  type VelocitySample,
  type WindField,
} from './windParticleCore';
import { useMap } from '@/components/map/MapContainer';

const MS_TO_KT = 1.9438444924;
/** 2 simulated hours per second of wall-clock time. */
const TIME_COMPRESSION = 7200;
/** Per-frame displacement clamp keeps fast cells readable (px). */
const MAX_PX_PER_FRAME = 4;
/** Trail persistence: multiply existing alpha by this each frame. */
const TRAIL_FADE = 0.94;
/** Coalesce fetches while the timeline scrubs (ms between requests). */
const FETCH_MIN_INTERVAL_MS = 700;

export type WindLayerMode = 'particles' | 'barbs';

interface WindParticleLayerProps {
  domain: NbmDomain;
  cycle: string; // YYYYMMDDHH
  fhour: number;
  mode: WindLayerMode;
  /** Rendered (and animating) only while true. */
  visible: boolean;
}

function metersPerPixel(zoom: number, latitudeDeg: number): number {
  const lat = Math.max(-85, Math.min(85, latitudeDeg));
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom);
}

function clampInt(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, Math.round(v)));
}

/** MapLibre v4 dropped `Map.getSize()` — read the container instead. */
function mapSize(map: MapLibreMap): { width: number; height: number } {
  const c = map.getContainer();
  return { width: c.clientWidth, height: c.clientHeight };
}

export function WindParticleLayer({
  domain,
  cycle,
  fhour,
  mode,
  visible,
}: WindParticleLayerProps) {
  const map = useMap();

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const trailCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const systemRef = useRef<WindParticleSystem | null>(null);
  const fieldRef = useRef<WindField | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastTimeRef = useRef(0);
  const movingRef = useRef(false);
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 });
  const fetchControllerRef = useRef<AbortController | null>(null);
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFetchAtRef = useRef(0);

  // Bump to trigger barbs redraw / particle restart after a fetch lands.
  const [fieldVersion, setFieldVersion] = useState(0);
  // Viewport fingerprint — changes on pan/zoom/resize, re-fetches the field.
  const [viewKey, setViewKey] = useState('init');

  // ── Map events + canvas lifecycle (once per map/mode/visibility) ─────────
  useEffect(() => {
    if (!map || !visible) return;

    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    let disposed = false;

    const applySize = () => {
      if (disposed) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      let trail = trailCanvasRef.current;
      if (!trail) {
        trail = document.createElement('canvas');
        trailCanvasRef.current = trail;
      }
      trail.width = canvas.width;
      trail.height = canvas.height;
      sizeRef.current = { w, h, dpr };
      systemRef.current?.resize(w, h);
    };

    applySize();
    const observer = new ResizeObserver(applySize);
    observer.observe(container);

    systemRef.current = new WindParticleSystem({
      width: sizeRef.current.w || 800,
      height: sizeRef.current.h || 600,
    });

    const onMoveStart = () => {
      movingRef.current = true;
      // Barbs are georeferenced to the fetched viewport — stale barbs are
      // worse than none while the camera is moving.
      if (mode === 'barbs') {
        const vctx = canvas.getContext('2d');
        if (vctx) {
          vctx.setTransform(1, 0, 0, 1, 0, 0);
          vctx.clearRect(0, 0, canvas.width, canvas.height);
        }
      }
    };
    const onMoveEnd = () => {
      movingRef.current = false;
      const b = map.getBounds();
      const s = mapSize(map);
      setViewKey(
        `${b.getWest().toFixed(3)}|${b.getSouth().toFixed(3)}|${b.getEast().toFixed(3)}|${b.getNorth().toFixed(3)}|${s.width}x${s.height}`,
      );
    };
    const onMapResize = () => {
      applySize();
      const b = map.getBounds();
      const s = mapSize(map);
      setViewKey(
        `${b.getWest().toFixed(3)}|${b.getSouth().toFixed(3)}|${b.getEast().toFixed(3)}|${b.getNorth().toFixed(3)}|${s.width}x${s.height}`,
      );
    };

    map.on('movestart', onMoveStart);
    map.on('moveend', onMoveEnd);
    map.on('resize', onMapResize);

    if (mode === 'particles') {
      // ── Animation loop (particles mode only) ─────────────────────────────
      const frame = (now: number) => {
        if (disposed) return;
        rafRef.current = requestAnimationFrame(frame);

        const vctx = canvas.getContext('2d');
        const tcanvas = trailCanvasRef.current;
        const tctx = tcanvas ? tcanvas.getContext('2d') : null;
        const sys = systemRef.current;
        const field = fieldRef.current;
        const { w, h, dpr } = sizeRef.current;
        if (!vctx || !tctx || !tcanvas || !sys || !field || w < 4 || h < 4) return;

        const last = lastTimeRef.current || now;
        const dt = Math.min(0.05, (now - last) / 1000);
        lastTimeRef.current = now;

        // Fade previous trails toward transparent (over the live map).
        tctx.save();
        tctx.setTransform(1, 0, 0, 1, 0, 0);
        tctx.globalCompositeOperation = 'destination-in';
        tctx.fillStyle = `rgba(0, 0, 0, ${TRAIL_FADE})`;
        tctx.fillRect(0, 0, tcanvas.width, tcanvas.height);
        tctx.restore();

        if (!movingRef.current && dt > 0) {
          const mpp = metersPerPixel(map.getZoom(), map.getCenter().lat);
          const frameSampler = (x: number, y: number): VelocitySample => {
            const ll = map.unproject([x, y]);
            const colF = ((ll.lng - field.minLon) / (field.maxLon - field.minLon)) * (field.cols - 1);
            const rowF = ((field.maxLat - ll.lat) / (field.maxLat - field.minLat)) * (field.rows - 1);
            const s = sampleWindField(field, colF, rowF);
            if (!s) return { vx: 0, vy: 0, speedKt: Number.NaN };
            return {
              // U is eastward (+screen x), V is northward (−screen y).
              vx: s.u * mpp * TIME_COMPRESSION,
              vy: -s.v * mpp * TIME_COMPRESSION,
              speedKt: Math.hypot(s.u, s.v) * MS_TO_KT,
            };
          };

          const samples = sys.step(frameSampler, dt);

          tctx.save();
          tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          tctx.globalCompositeOperation = 'source-over';
          tctx.lineWidth = 1.2;
          tctx.lineCap = 'round';
          for (let i = 0; i < sys.particles.length; i++) {
            const p = sys.particles[i];
            const s = samples[i];
            if (!p || !s) continue;
            const dx = p.x - p.prevX;
            const dy = p.y - p.prevY;
            if (dx * dx + dy * dy < 0.0025) continue; // respawned / calm
            tctx.strokeStyle = windSpeedColor(Number.isFinite(s.speedKt) ? s.speedKt : 0, 0.85);
            tctx.beginPath();
            tctx.moveTo(p.prevX, p.prevY);
            tctx.lineTo(p.x, p.y);
            tctx.stroke();
          }
          tctx.restore();
        }

        // Composite: visible canvas = fresh trail frame.
        vctx.setTransform(1, 0, 0, 1, 0, 0);
        vctx.clearRect(0, 0, canvas.width, canvas.height);
        vctx.drawImage(tcanvas, 0, 0);
      };
      lastTimeRef.current = 0;
      rafRef.current = requestAnimationFrame(frame);
    }

    return () => {
      disposed = true;
      // 1) Stop the animation loop.
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      // 2) Detach observers + map listeners.
      observer.disconnect();
      map.off('movestart', onMoveStart);
      map.off('moveend', onMoveEnd);
      map.off('resize', onMapResize);
      // 3) Destroy the particle pool (empties its backing arrays).
      systemRef.current?.destroy();
      systemRef.current = null;
      // 4) Drop field + trail storage and clear the visible canvas.
      fieldRef.current = null;
      trailCanvasRef.current = null;
      const vctx = canvas.getContext('2d');
      if (vctx) {
        vctx.setTransform(1, 0, 0, 1, 0, 0);
        vctx.clearRect(0, 0, canvas.width, canvas.height);
      }
      lastTimeRef.current = 0;
      movingRef.current = false;
    };
  }, [map, visible, mode]);

  // ── U/V field fetch (viewport + forecast context) ─────────────────────────
  useEffect(() => {
    if (!map || !visible) return;
    let cancelled = false;

    const doFetch = () => {
      const b = map.getBounds();
      const minLon = b.getWest();
      const minLat = b.getSouth();
      const maxLon = b.getEast();
      const maxLat = b.getNorth();
      if (![minLon, minLat, maxLon, maxLat].every(Number.isFinite)) return;
      const { width, height } = mapSize(map);
      const aspect = width / Math.max(1, height);
      const cols = clampInt(96 * aspect, 48, 256);
      const rows = clampInt(96, 48, 256);

      fetchControllerRef.current?.abort();
      const controller = new AbortController();
      fetchControllerRef.current = controller;

      api
        .probeWindField({
          domain,
          cycle,
          fhour,
          bbox: [minLon, minLat, maxLon, maxLat],
          cols,
          rows,
          units: 'metric',
          signal: controller.signal,
        })
        .then((resp) => {
          if (cancelled || controller.signal.aborted) return;
          fieldRef.current = windFieldFromResponse(resp);
          setFieldVersion((v) => v + 1);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (err instanceof DOMException && err.name === 'AbortError') return;
          // Stay on the previous field (if any) rather than blanking.
          console.warn('[wind-field] fetch failed:', err);
        });
    };

    // Coalesce rapid forecast-hour changes (timeline playback): the latest
    // frame always wins via a trailing request.
    const sinceLast = Date.now() - lastFetchAtRef.current;
    if (sinceLast >= FETCH_MIN_INTERVAL_MS) {
      lastFetchAtRef.current = Date.now();
      doFetch();
    } else {
      if (fetchTimerRef.current !== null) clearTimeout(fetchTimerRef.current);
      fetchTimerRef.current = setTimeout(() => {
        fetchTimerRef.current = null;
        lastFetchAtRef.current = Date.now();
        doFetch();
      }, FETCH_MIN_INTERVAL_MS - sinceLast);
    }

    return () => {
      cancelled = true;
      if (fetchTimerRef.current !== null) {
        clearTimeout(fetchTimerRef.current);
        fetchTimerRef.current = null;
      }
      fetchControllerRef.current?.abort();
      fetchControllerRef.current = null;
    };
  }, [map, visible, domain, cycle, fhour, viewKey, mode]);

  // ── Barbs redraw (barbs mode: on data arrival, resize, camera settle) ─────
  useEffect(() => {
    if (!map || !visible || mode !== 'barbs' || fieldVersion === 0) return;
    const canvas = canvasRef.current;
    const field = fieldRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !field) return;

    const { w, h, dpr } = sizeRef.current;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (w < 4 || h < 4) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { cols, rows, minLon, maxLon, minLat, maxLat, u, v } = field;
    const zoom = map.getZoom();
    const barbSize = zoom < 5 ? 11 : 14;
    const stepCol = Math.max(1, Math.round(cols / 44));
    const stepRow = Math.max(1, Math.round(rows / 30));

    for (let r = 0; r < rows; r += stepRow) {
      for (let c = 0; c < cols; c += stepCol) {
        const idx = r * cols + c;
        const uu = u[idx];
        const vv = v[idx];
        if (
          typeof uu !== 'number' ||
          typeof vv !== 'number' ||
          !Number.isFinite(uu) ||
          !Number.isFinite(vv)
        ) {
          continue;
        }
        const lon = minLon + (c / (cols - 1)) * (maxLon - minLon);
        const lat = maxLat - (r / (rows - 1)) * (maxLat - minLat);
        const pt = map.project([lon, lat]);
        const speedKt = Math.hypot(uu, vv) * MS_TO_KT;
        drawWindBarb(ctx, pt.x, pt.y, windDirectionFromUV(uu, vv), speedKt, {
          size: barbSize,
          color: windSpeedColor(speedKt, 0.85),
          lineWidth: 1.3,
        });
      }
    }
  }, [map, visible, mode, fieldVersion, viewKey]);

  if (!visible || !map) return null;

  return (
    <div ref={containerRef} className="pointer-events-none absolute inset-0 z-10 overflow-hidden">
      <canvas ref={canvasRef} aria-hidden className="absolute inset-0" />
      <div className="absolute bottom-3 left-3 rounded-md border border-white/10 bg-[#0a1120]/85 px-2.5 py-1.5 shadow-lg backdrop-blur">
        <div className="text-[9px] font-semibold uppercase tracking-wide text-white/55">
          {mode === 'particles' ? '10 m wind particles' : '10 m wind barbs'}
        </div>
        <div className="mt-1 h-1.5 w-28 rounded-full" style={{ background: windSpeedGradient() }} />
        <div className="mt-0.5 flex justify-between font-mono text-[8px] text-white/45">
          <span>0</span>
          <span>25</span>
          <span>50+ kt</span>
        </div>
      </div>
    </div>
  );
}
