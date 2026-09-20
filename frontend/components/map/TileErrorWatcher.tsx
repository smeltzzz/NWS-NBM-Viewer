'use client';

/**
 * Tile failure → friendly toast.
 *
 * MapLibre reports raster tile request failures through the map's `error`
 * event (and quietly renders the gap). That is exactly the UX hole this
 * watcher closes: if a *specific product* — typically a probabilistic one
 * (percentiles, POP/QPF exceedance) that the QMD stream does not publish for
 * a given forecast hour, or a cycle where NOAA delayed an hour — returns
 * 404/422/503, we explain it once per (element, hour) instead of leaving the
 * user staring at an empty map.
 *
 * Rules:
 *  * only `/api/v1/tiles/` failures are considered — basemap/vector noise is ignored;
 *  * 200/204/304-ish partial errors are ignored (204 is a legitimate
 *    "off the domain footprint" answer);
 *  * per-key throttling: at most one toast every 20 s per element+hour+status,
 *    so a whole viewport of failing tiles produces one message, not thirty.
 */

import { useEffect, useRef } from 'react';

import { useToast } from '@/components/common/ToastProvider';

import { useMap } from './MapContainer';

const TILE_URL_RE = /\/api\/v1\/tiles\/([^/]+)\/(\d{8,10})\/([^/]+)\/(f?\d+)/;
const THROTTLE_MS = 20_000;
const BENIGN_STATUSES = new Set([200, 204, 304, 499]);

type TileErrorDetail = {
  domain: string;
  cycle: string;
  element: string;
  fhour: number;
  status: number | null;
};

function describeError(error: unknown): TileErrorDetail | null {
  const err = (error ?? {}) as Record<string, unknown> & { request?: Record<string, unknown> };
  const request = (err.request ?? {}) as Record<string, unknown>;
  const url =
    (typeof err.url === 'string' && err.url) ||
    (typeof request.url === 'string' && request.url) ||
    (typeof err.message === 'string' && err.message) ||
    '';
  const match = url.match(TILE_URL_RE);
  if (!match) return null;
  const rawStatus = err.status ?? err.http_status ?? err.statusCode ?? request.status;
  const status = typeof rawStatus === 'number' && Number.isFinite(rawStatus) ? rawStatus : null;
  if (status !== null && BENIGN_STATUSES.has(status)) return null;
  // Message-based errors without a status that mention the tile path are
  // usually AbortError / network drop — transient, not worth a toast.
  if (status === null) return null;
  return {
    domain: match[1] as string,
    cycle: match[2] as string,
    element: (match[3] as string).toLowerCase(),
    fhour: Number((match[4] as string).replace(/^f/i, '')),
    status,
  };
}

const PROBABILISTIC = /(_p\d+|pop\d+|gt_|pqpf|percentile)/;

function copyFor(detail: TileErrorDetail): { title: string; message: string } {
  const hour = `F${String(detail.fhour).padStart(3, '0')}`;
  if (detail.status === 404 || detail.status === 422) {
    const probabilistic = PROBABILISTIC.test(detail.element);
    return {
      title: `${detail.element.toUpperCase()} is not available at ${hour}`,
      message: probabilistic
        ? `NOAA's probabilistic stream does not publish ${detail.element} for ${hour} in cycle ${detail.cycle}. Pick an adjacent forecast hour, or switch back to the deterministic product.`
        : `${detail.element} for ${hour} has not been published for cycle ${detail.cycle} yet — NOAA publishes late hours progressively. The viewer will pick it up automatically.`,
    };
  }
  return {
    title: 'Tile service is busy',
    message: `NODD is rate-limiting tile renders right now (${detail.status}). Cached frames keep showing while the service backs off.`,
  };
}

export function TileErrorWatcher() {
  const map = useMap();
  const toast = useToast();
  const lastShown = useRef(new Map<string, number>());

  useEffect(() => {
    if (!map) return;
    const onError = (event: { error?: unknown }) => {
      const detail = describeError(event?.error);
      if (!detail) return;
      const key = `tile:${detail.element}:${detail.fhour}:${detail.status}`;
      const now = Date.now();
      const prev = lastShown.current.get(key) ?? 0;
      if (now - prev < THROTTLE_MS) return;
      lastShown.current.set(key, now);
      const { title, message } = copyFor(detail);
      toast.push({
        key,
        tone: detail.status === 404 || detail.status === 422 ? 'warning' : 'info',
        title,
        message,
        ttlMs: detail.status === 503 || detail.status === 429 ? 12_000 : 9_000,
      });
    };
    map.on('error', onError);
    return () => {
      map.off('error', onError);
    };
  }, [map, toast]);

  return null;
}
