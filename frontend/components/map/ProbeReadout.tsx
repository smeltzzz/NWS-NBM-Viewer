'use client';

/**
 * Interactive hover coordinate & cursor readout.
 *
 * On `mousemove` over the canvas we debounce/throttle the pointer position
 * (130 ms minimum cadence, in-flight requests aborted when the cursor jumps
 * far, all requests cancellable) and query
 * `/api/v1/probe/point?lat=..&lon=..&domain=..&cycle=..&fhour=..&units=..`.
 *
 * A floating HUD badge tracks the cursor with the *nearest city* (taken from
 * the already-loaded NWS CWA polygons — a local `queryRenderedFeatures`, no
 * extra traffic) plus the real-time bilinearly interpolated element value,
 * e.g. "Denver: 74.2 °F" or "Wind: 18 kt".
 *
 * 60 FPS discipline: cursor tracking mutates the badge's transform directly
 * in the event handler (no React state per mousemove); React only re-renders
 * when a probe response actually arrives, and never while dragging.
 */

import mapboxgl from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { ELEMENTS } from '@/lib/nbm';
import { formatLatLon, formatProbeValue, forecastHourLabel } from '@/lib/format';
import type { NbmDomain } from '@/lib/types';

import { CWAA_SOURCE_ID, OVERLAY_LAYERS } from './layerIds';
import { useMap } from './MapContainer';

const PROBE_MIN_INTERVAL_MS = 130;
const PROBE_JUMP_PX = 28;

interface ProbeReadoutProps {
  domain: NbmDomain;
  cycle: string; // YYYYMMDDHH
  fhour: number;
  element: string;
  units?: 'imperial' | 'metric';
}

interface Readout {
  lat: number;
  lon: number;
  city: string | null;
  primaryLabel: string | null;
  primaryValue: string | null;
  secondary: string | null;
  note: string | null; // e.g. "outside NBM domain"
}

export function ProbeReadout({
  domain,
  cycle,
  fhour,
  element,
  units = 'imperial',
}: ProbeReadoutProps) {
  const map = useMap();
  const badgeRef = useRef<HTMLDivElement | null>(null);

  // Latest params for event handlers (avoid re-binding on every render).
  const paramsRef = useRef({ domain, cycle, fhour, element, units });
  paramsRef.current = { domain, cycle, fhour, element, units };

  const [readout, setReadout] = useState<Readout | null>(null);
  const [hovering, setHovering] = useState(false);

  const inFlightRef = useRef<AbortController | null>(null);
  const seqRef = useRef(0);
  const lastRequestAtRef = useRef(0);
  const lastProbePointRef = useRef<{ x: number; y: number } | null>(null);
  const draggingRef = useRef(false);
  const hoveringRef = useRef(false);

  useEffect(() => {
    if (!map) return;
    const canvas = map.getCanvas();

    const badge = () => badgeRef.current;

    const showBadge = () => {
      if (!hoveringRef.current) {
        hoveringRef.current = true;
        setHovering(true);
      }
    };
    const hideBadge = () => {
      if (hoveringRef.current) {
        hoveringRef.current = false;
        setHovering(false);
      }
      setReadout(null);
      inFlightRef.current?.abort();
      inFlightRef.current = null;
    };

    const positionBadge = (x: number, y: number) => {
      const el = badge();
      if (!el) return;
      const parent = el.parentElement;
      const px = x + 16 + el.offsetWidth > (parent?.clientWidth ?? 0) - 12 ? x - el.offsetWidth - 16 : x + 16;
      const py = y + 18 + el.offsetHeight > (parent?.clientHeight ?? 0) - 12 ? y - el.offsetHeight - 14 : y + 18;
      el.style.transform = `translate(${px}px, ${py}px)`;
    };

    const cityAt = (lon: number, lat: number): string | null => {
      try {
        if (!map.getSource(CWAA_SOURCE_ID)) return null;
        const point = map.project([lon, lat]);
        const features = map.queryRenderedFeatures(point, {
          layers: [OVERLAY_LAYERS.cwaaFill],
        }) as Array<{ properties?: Record<string, unknown> }>;
        const p = features[0]?.properties;
        const city = p?.city as string | undefined;
        const st = p?.st as string | undefined;
        if (city) return st ? `${city}, ${st}` : city;
        return null;
      } catch {
        return null;
      }
    };

    const fireProbe = (lon: number, lat: number, x: number, y: number) => {
      const { domain: d, cycle: c, fhour: fh, element: el, units: u } = paramsRef.current;
      lastProbePointRef.current = { x, y };
      lastRequestAtRef.current = performance.now();
      inFlightRef.current?.abort();
      const controller = new AbortController();
      inFlightRef.current = controller;
      const seq = ++seqRef.current;

      const wanted = Array.from(new Set([el, 'tmp', 'wind', 'gust']));

      api
        .probePoint({
          lat,
          lon,
          domain: d,
          cycle: c,
          fhour: fh,
          elements: wanted,
          units: u,
          signal: controller.signal,
        })
        .then((res) => {
          if (seq !== seqRef.current) return;
          const elValue = res.values?.[el];
          const primaryLabel = ELEMENTS.find((e) => e.key === el)?.label ?? el;
          const primaryValue = formatProbeValue(elValue ?? { value: null, units: '' });
          const summary = res.summary ?? {};
          const secondaryRaw =
            el === 'wind'
              ? summary.wind_gust
              : summary.wind ?? (summary.temperature ? `Temp ${summary.temperature}` : null);
          setReadout({
            lat,
            lon,
            city: cityAt(lon, lat),
            primaryLabel,
            primaryValue: primaryValue ?? (summary.temperature ? summary.temperature : null),
            secondary: secondaryRaw ?? null,
            note: null,
          });
          positionBadge(x, y);
        })
        .catch((err) => {
          if (err instanceof DOMException && err.name === 'AbortError') return;
          if (seq !== seqRef.current) return;
          const status = (err as { status?: number })?.status;
          setReadout({
            lat,
            lon,
            city: cityAt(lon, lat),
            primaryLabel: null,
            primaryValue: null,
            secondary: null,
            note:
              status === 422
                ? 'outside NBM domain'
                : status === 404
                  ? 'no data for this element'
                  : 'probe unavailable',
          });
        });
    };

    const onMouseMove = (e: mapboxgl.MapMouseEvent) => {
      if (draggingRef.current) return;
      const x = e.point.x;
      const y = e.point.y;
      const { lngLat } = e;
      showBadge();
      positionBadge(x, y);

      const now = performance.now();
      const inFlight = inFlightRef.current;
      const last = lastProbePointRef.current;
      const jumped = last ? Math.hypot(x - last.x, y - last.y) >= PROBE_JUMP_PX : false;
      if (!inFlight && now - lastRequestAtRef.current < PROBE_MIN_INTERVAL_MS && !jumped) return;
      if (inFlight && !jumped) return; // coalesce: let the current probe answer
      fireProbe(lngLat.lng, lngLat.lat, x, y);
    };

    const onDragStart = () => {
      draggingRef.current = true;
      hideBadge();
    };
    const onDragEnd = () => {
      draggingRef.current = false;
      lastProbePointRef.current = null;
      lastRequestAtRef.current = 0;
    };

    const onMouseLeave = () => {
      draggingRef.current = false;
      hideBadge();
    };

    map.on('mousemove', onMouseMove);
    map.on('dragstart', onDragStart);
    map.on('dragend', onDragEnd);
    canvas.addEventListener('mouseleave', onMouseLeave);

    return () => {
      map.off('mousemove', onMouseMove);
      map.off('dragstart', onDragStart);
      map.off('dragend', onDragEnd);
      canvas.removeEventListener('mouseleave', onMouseLeave);
      inFlightRef.current?.abort();
      inFlightRef.current = null;
      draggingRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  const elementLabel = ELEMENTS.find((e) => e.key === element)?.label ?? element;

  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden" aria-hidden={!hovering}>
      <div
        ref={badgeRef}
        className={`absolute left-0 top-0 will-change-transform ${
          hovering ? 'opacity-100' : 'pointer-events-none opacity-0'
        } transition-opacity duration-75`}
      >
        <div className="min-w-[150px] rounded-md border border-white/15 bg-surface-raised/95 px-2.5 py-1.5 shadow-panel backdrop-blur">
          {readout ? (
            <>
              <div className="font-mono text-[10px] leading-4 text-white/50">
                {formatLatLon(readout.lat, readout.lon)}
              </div>
              {readout.primaryValue ? (
                <div className="text-xs font-semibold leading-5 text-white">
                  {readout.city ? `${readout.city}: ` : ''}
                  {readout.primaryLabel && readout.primaryLabel !== elementLabel
                    ? `${readout.primaryLabel} `
                    : ''}
                  {readout.primaryValue}
                </div>
              ) : readout.note ? (
                <div className="text-xs font-medium leading-5 text-amber-300/90">{readout.note}</div>
              ) : (
                <div className="text-xs font-medium leading-5 text-white/60">…</div>
              )}
              <div className="text-[10px] leading-4 text-white/55">
                {readout.secondary ? `${readout.secondary} · ` : ''}
                <span className="font-mono text-accent/90">{forecastHourLabel(fhour)}</span>
              </div>
            </>
          ) : (
            <div className="text-xs text-white/60">…</div>
          )}
        </div>
      </div>
    </div>
  );
}
