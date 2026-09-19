'use client';

/**
 * MapLibre GL wrapper: owns lifecycle, state reconciliation and exports an
 * imperative handle so sibling controls (layer panel, legend) can drive panning
 * and source swapping without prop-drilling through React context.
 */

import mapboxgl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import { basemapStyle } from '@/lib/basemaps';
import { config } from '@/lib/config';
import { tileUrl } from '@/lib/nbm';
import type { NbmDomain, NbmProduct } from '@/lib/types';

export interface MapHandle {
  flyTo: (center: [number, number], zoom: number) => void;
  setNbmLayer: (layer: NbmLayer) => void;
}

export interface NbmLayer {
  variable: string;
  forecastHour: number;
  domain: NbmDomain;
  product: NbmProduct;
}

interface MapViewProps {
  initialLayer: NbmLayer;
  className?: string;
  onReady?: () => void;
}

const NBM_SOURCE_ID = 'nbm-source';
const NBM_LAYER_ID = 'nbm-layer';

export const MapView = forwardRef<MapHandle, MapViewProps>(function MapView(
  { initialLayer, className, onReady },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const layerRef = useRef<NbmLayer>(initialLayer);
  const [map, setMap] = useState<mapboxgl.Map | null>(null);

  // ── Mount ────────────────────────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const instance = new mapboxgl.Map({
      container,
      style: basemapStyle(config.map.basemap),
      center: config.map.center,
      zoom: config.map.zoom,
      maxZoom: 14,
      attributionControl: { compact: true },
    });

    // Standard map controls (compact, dark-UI friendly).
    instance.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), 'top-left');
    instance.addControl(new mapboxgl.ScaleControl({ maxWidth: 160, unit: 'metric' }), 'bottom-left');

    mapRef.current = instance;
    setMap(instance);

    instance.on('load', () => {
      upsertNbmLayer(instance, layerRef.current);
      onReady?.();
    });

    return () => {
      instance.remove();
      mapRef.current = null;
      setMap(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Imperative handle ────────────────────────────────────────────────────
  useImperativeHandle(ref, () => ({
    flyTo: (center, zoom) => {
      mapRef.current?.flyTo({ center, zoom, duration: 1100 });
    },
    setNbmLayer: (layer) => {
      layerRef.current = layer;
      if (mapRef.current?.isStyleLoaded()) {
        upsertNbmLayer(mapRef.current, layer);
      }
    },
  }));

  return <div ref={containerRef} className={className} aria-label="NBM map" />;
});

/** Add or live-update the NBM raster layer (no full reload of the style). */
function upsertNbmLayer(map: mapboxgl.Map, layer: NbmLayer): void {
  const existingLayer = map.getLayer(NBM_LAYER_ID);
  const source = map.getSource(NBM_SOURCE_ID);

  // Hide the overlay while re-pointing the source to avoid a stale flash.
  if (existingLayer) {
    map.setLayoutProperty(NBM_LAYER_ID, 'visibility', 'none');
  }

  if (source) {
    (source as mapboxgl.RasterTileSource).setTiles([
      tileUrl(layer.variable, layer.forecastHour, layer.product, layer.domain),
    ]);
  } else {
    map.addSource(NBM_SOURCE_ID, {
      type: 'raster',
      tiles: [tileUrl(layer.variable, layer.forecastHour, layer.product, layer.domain)],
      tileSize: 256,
      minzoom: 0,
      maxzoom: 12,
    });
    map.addLayer({
      id: NBM_LAYER_ID,
      type: 'raster',
      source: NBM_SOURCE_ID,
      paint: {
        'raster-opacity': 0.86,
        'raster-resampling': 'linear',
        'raster-fade-duration': 250,
      },
    });
  }

  if (existingLayer) {
    map.setLayoutProperty(NBM_LAYER_ID, 'visibility', 'visible');
  }
}
