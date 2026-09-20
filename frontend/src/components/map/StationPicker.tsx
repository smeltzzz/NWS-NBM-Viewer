'use client';

/**
 * Map click → station pick for the meteogram.
 *
 * Registers a single `click` listener on the map (MapLibre suppresses the
 * synthetic click after drag pans, so this only fires on genuine clicks).
 * The nearest city is resolved from the already-loaded NWS CWA polygons via
 * `queryRenderedFeatures` — no extra network traffic.
 *
 * The `onPick` callback is kept in a ref so the listener is bound exactly
 * once per map instance and removed on unmount.
 */

import { useEffect, useRef } from 'react';

import mapboxgl from 'maplibre-gl';

import { CWAA_SOURCE_ID, OVERLAY_LAYERS } from '@/components/map/layerIds';
import { useMap } from '@/components/map/MapContainer';

export interface StationPick {
  lat: number;
  lon: number;
  city: string | null;
}

interface StationPickerProps {
  onPick: (pick: StationPick) => void;
}

export function StationPicker({ onPick }: StationPickerProps) {
  const map = useMap();
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  useEffect(() => {
    if (!map) return;

    const cityAt = (lng: number, lat: number): string | null => {
      try {
        if (!map.getSource(CWAA_SOURCE_ID)) return null;
        const point = map.project([lng, lat]);
        const features = map.queryRenderedFeatures(point, {
          layers: [OVERLAY_LAYERS.cwaaFill],
        }) as Array<{ properties?: Record<string, unknown> }>;
        const p = features[0]?.properties;
        const city = p?.city as string | undefined;
        const st = p?.st as string | undefined;
        if (!city) return null;
        return st ? `${city}, ${st}` : city;
      } catch {
        return null;
      }
    };

    const handleClick = (e: mapboxgl.MapMouseEvent) => {
      const { lat, lng } = e.lngLat;
      onPickRef.current?.({ lat, lon: lng, city: cityAt(lng, lat) });
    };

    map.on('click', handleClick);
    return () => {
      map.off('click', handleClick);
    };
  }, [map]);

  return null;
}
