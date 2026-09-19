'use client';

/**
 * Dynamic NBM raster tile layer.
 *
 * Synchronizes one MapLibre raster source/layer with the active NBM state
 * (domain, cycle, element, forecast hour) and the opacity slider:
 *
 *   source: `/api/v1/tiles/{domain}/{cycle}/{element}/{fhour}/{z}/{x}/{y}.webp`
 *   paint:  raster-opacity      — dynamic, bound to the state slider (0.82)
 *           raster-resampling   — `linear` (smooth bilinear, no blockiness)
 *           raster-fade-duration— 150 ms cross-fade between forecast frames
 *
 * Flicker-free time scrubbing ("double buffering"):
 *   MapLibre keeps the previously loaded tiles on screen until the re-pointed
 *   tiles arrive, then cross-fades them over `raster-fade-duration`. We layer
 *   a coalescing state machine on top: while a `setTiles()` re-point is still
 *   settling (new tiles in flight), further scrub steps are queued as a
 *   single *pending* key and applied as soon as the source reports it has
 *   settled (a `sourcedata` event; a 900 ms fallback guards against stuck
 *   loads). Rapid slider drags therefore collapse into one clean transition
 *   per settled frame — the final position always wins, and no frame ever
 *   blank-then-redraws.
 */

import mapboxgl from 'maplibre-gl';
import { useEffect, useRef } from 'react';

import { nbmTileUrl } from '@/lib/nbm';
import type { NbmDomain } from '@/lib/types';

import { NBM_LAYER_ID, NBM_SOURCE_ID } from './layerIds';
import { useMap } from './MapContainer';

/** Default layer opacity (bound to the toolbar slider). */
export const RASTER_DEFAULT_OPACITY = 0.82;
/** Cross-fade between forecast frames (ms). */
const RASTER_FADE_DURATION_MS = 150;
/** Max zoom the tile service renders (backend `tile_max_zoom`). */
const TILE_MAX_ZOOM = 12;
/** Give up waiting for a source to settle after this long (ms). */
const SETTLE_TIMEOUT_MS = 900;

interface WeatherRasterLayerProps {
  domain: NbmDomain;
  cycle: string; // YYYYMMDDHH
  element: string;
  fhour: number;
  /** Layer opacity, 0–1 (bound to the state slider). Default 0.82. */
  opacity?: number;
  visible?: boolean;
}

export function WeatherRasterLayer({
  domain,
  cycle,
  element,
  fhour,
  opacity = RASTER_DEFAULT_OPACITY,
  visible = true,
}: WeatherRasterLayerProps) {
  const map = useMap();

  // Latest state for use inside long-lived map event handlers.
  const stateRef = useRef({ domain, cycle, element, fhour, opacity, visible });
  stateRef.current = { domain, cycle, element, fhour, opacity, visible };

  // Double-buffer bookkeeping (plain refs — never triggers re-renders).
  const appliedKeyRef = useRef<string | null>(null);
  const pendingRef = useRef<{ key: string; url: string } | null>(null);
  const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keyRequestRef = useRef<((key: string, url: string) => void) | null>(null);

  const layerKey = `${domain}|${cycle}|${element}|${fhour}`;

  // ── Setup: source + layer, once per map instance ─────────────────────────
  useEffect(() => {
    if (!map) return;

    // A fresh map instance (e.g. React StrictMode remount) starts clean.
    appliedKeyRef.current = null;
    pendingRef.current = null;

    const source = () =>
      map.getSource(NBM_SOURCE_ID) as mapboxgl.RasterTileSource | undefined;

    const applyPending = () => {
      const pending = pendingRef.current;
      const src = source();
      if (!pending || !src) return;
      pendingRef.current = null;
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current);
        settleTimerRef.current = null;
      }
      // Old tiles stay on screen until the new ones arrive; the layer then
      // cross-fades over `raster-fade-duration` — no blank flash.
      src.setTiles([pending.url]);
      appliedKeyRef.current = pending.key;
    };

    const tryApplyPending = () => {
      const pending = pendingRef.current;
      const src = source();
      if (!pending || !src) return;
      if (src.loaded()) {
        applyPending();
      } else if (!settleTimerRef.current) {
        // Stuck-load guard: never freeze the scrubber.
        settleTimerRef.current = setTimeout(applyPending, SETTLE_TIMEOUT_MS);
      }
    };

    const requestKey = (key: string, url: string) => {
      if (key === appliedKeyRef.current || key === pendingRef.current?.key) return;
      pendingRef.current = { key, url };
      tryApplyPending();
    };
    keyRequestRef.current = requestKey;

    const ensureLayer = () => {
      if (!map.getSource(NBM_SOURCE_ID)) {
        const s = stateRef.current;
        map.addSource(NBM_SOURCE_ID, {
          type: 'raster',
          tiles: [nbmTileUrl(s.domain, s.cycle, s.element, s.fhour)],
          tileSize: 256,
          minzoom: 0,
          maxzoom: TILE_MAX_ZOOM,
          attribution: 'NOAA/NWS NBM',
        });
      }
      if (!map.getLayer(NBM_LAYER_ID)) {
        map.addLayer({
          id: NBM_LAYER_ID,
          type: 'raster',
          source: NBM_SOURCE_ID,
          paint: {
            'raster-opacity': stateRef.current.opacity,
            'raster-resampling': 'linear',
            'raster-fade-duration': RASTER_FADE_DURATION_MS,
          },
        });
      }
      // (Re)assert paint/layout state after any style reload.
      map.setPaintProperty(NBM_LAYER_ID, 'raster-opacity', stateRef.current.opacity);
      map.setLayoutProperty(NBM_LAYER_ID, 'visibility', stateRef.current.visible ? 'visible' : 'none');
      // A style reload rebuilt the source from scratch — re-request the key.
      appliedKeyRef.current = null;
      const s = stateRef.current;
      requestKey(
        `${s.domain}|${s.cycle}|${s.element}|${s.fhour}`,
        nbmTileUrl(s.domain, s.cycle, s.element, s.fhour),
      );
    };

    const onSourceData = (e: mapboxgl.MapSourceDataEvent) => {
      if (e.sourceId !== NBM_SOURCE_ID) return;
      tryApplyPending();
    };

    const off: Array<() => void> = [];

    if (map.isStyleLoaded()) {
      ensureLayer();
    } else {
      map.on('style.load', ensureLayer);
      off.push(() => map.off('style.load', ensureLayer));
    }
    map.on('sourcedata', onSourceData);
    off.push(() => map.off('sourcedata', onSourceData));

    return () => {
      for (const fn of off) fn();
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current);
        settleTimerRef.current = null;
      }
      appliedKeyRef.current = null;
      pendingRef.current = null;
      keyRequestRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // ── Forecast key changes (scrubbing, element/domain/cycle switches) ──────
  useEffect(() => {
    keyRequestRef.current?.(
      layerKey,
      nbmTileUrl(domain, cycle, element, fhour),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, layerKey]);

  // ── Opacity slider (pure paint update — no tile work, no flicker) ────────
  useEffect(() => {
    if (map?.getLayer(NBM_LAYER_ID)) {
      map.setPaintProperty(NBM_LAYER_ID, 'raster-opacity', opacity);
    }
  }, [map, opacity]);

  // ── Visibility ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (map?.getLayer(NBM_LAYER_ID)) {
      map.setLayoutProperty(NBM_LAYER_ID, 'visibility', visible ? 'visible' : 'none');
    }
  }, [map, visible]);

  return null;
}
