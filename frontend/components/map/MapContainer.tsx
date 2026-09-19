'use client';

/**
 * Map engine initialization.
 *
 * Owns the full-screen MapLibre GL JS canvas: lifecycle, high-DPI rendering,
 * the dark vector basemap, per-NBM-domain camera presets and the controls.
 * Children receive the live map instance through `MapContext` (see
 * `useMap()`) so the weather raster, vector overlays and probe readout can
 * attach sources/layers without prop-drilling.
 *
 * 60 FPS notes:
 *  - `pixelRatio: window.devicePixelRatio` keeps the canvas crisp on
 *    HiDPI without oversampling beyond the display density.
 *  - Rotation is disabled (`dragRotate: false`) — the NBM rasters are
 *    north-up gridded data, so 2D-only gestures skip an entire class of
 *    per-frame transforms.
 *  - This component renders no per-frame React state; all gesture feedback
 *    lives in the map renderer itself.
 */

import mapboxgl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { basemapStyle } from '@/lib/basemaps';
import { config } from '@/lib/config';
import { DOMAIN_VIEWPORTS } from '@/lib/nbm';
import type { NbmDomain } from '@/lib/types';

const MapContext = createContext<mapboxgl.Map | null>(null);

/** Access the live map instance from any child of `<MapContainer>`. */
export function useMap(): mapboxgl.Map | null {
  return useContext(MapContext);
}

export interface MapHandle {
  /** Fly the camera to an absolute position. */
  flyTo: (center: [number, number], zoom: number) => void;
  /** Fly to the canonical viewport for an NBM domain. */
  flyToDomain: (domain: NbmDomain) => void;
  /** Raw map instance (escape hatch for advanced controls). */
  getMap: () => mapboxgl.Map | null;
}

interface MapContainerProps {
  /** Active NBM domain — sets the initial camera and re-frames on change. */
  domain: NbmDomain;
  /** Basemap style key ("dark" vector is the default). */
  basemap?: string;
  className?: string;
  children?: React.ReactNode;
}

export const MapContainer = forwardRef<MapHandle, MapContainerProps>(function MapContainer(
  { domain, basemap, className, children },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const lastDomainRef = useRef<NbmDomain>(domain);
  const [map, setMap] = useState<mapboxgl.Map | null>(null);

  // ── Mount / unmount ───────────────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const viewport = DOMAIN_VIEWPORTS[domain] ?? DOMAIN_VIEWPORTS.co;

    const instance = new mapboxgl.Map({
      container,
      style: basemapStyle(basemap ?? config.map.basemap),
      center: viewport.center,
      zoom: viewport.zoom,
      minZoom: 1.5,
      // NBM data is 1.25–10 km gridded; zooming past 12 only upsamples.
      maxZoom: 12,
      // High-DPI support: render at native device pixel ratio.
      pixelRatio: window.devicePixelRatio,
      attributionControl: { compact: true },
      // 2D weather viewing: no rotation (cheaper + rasters are north-up).
      dragRotate: false,
      // Keep panning/zooming smooth while style-dependent layers settle.
      refreshExpiredTiles: true,
    });

    instance.addControl(
      new mapboxgl.NavigationControl({ visualizePitch: true }),
      'top-right',
    );
    instance.addControl(new mapboxgl.ScaleControl({ maxWidth: 160, unit: 'metric' }), 'bottom-left');

    mapRef.current = instance;
    setMap(instance);

    return () => {
      instance.remove();
      mapRef.current = null;
      setMap(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Domain → camera preset ────────────────────────────────────────────────
  useEffect(() => {
    if (domain === lastDomainRef.current) return;
    lastDomainRef.current = domain;
    const viewport = DOMAIN_VIEWPORTS[domain];
    if (!viewport) return;
    mapRef.current?.flyTo({
      center: viewport.center,
      zoom: viewport.zoom,
      duration: 1100,
      essential: true,
    });
  }, [domain]);

  // ── Imperative handle ─────────────────────────────────────────────────────
  const flyTo = useCallback((center: [number, number], zoom: number) => {
    mapRef.current?.flyTo({ center, zoom, duration: 1100 });
  }, []);

  const flyToDomain = useCallback((next: NbmDomain) => {
    const viewport = DOMAIN_VIEWPORTS[next];
    if (viewport) {
      mapRef.current?.flyTo({
        center: viewport.center,
        zoom: viewport.zoom,
        duration: 1100,
        essential: true,
      });
    }
  }, []);

  useImperativeHandle(ref, () => ({
    flyTo,
    flyToDomain,
    getMap: () => mapRef.current,
  }));

  return (
    <MapContext.Provider value={map}>
      <div
        ref={containerRef}
        className={className}
        role="application"
        aria-label="NBM weather map"
        style={{ position: 'absolute', inset: 0 }}
      >
        {children}
      </div>
    </MapContext.Provider>
  );
});
