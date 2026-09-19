'use client';

/**
 * Geographical vector overlays, stacked above the weather raster.
 *
 *   - US State lines      — thin crisp #ffffff @ 0.4 opacity (always on when enabled)
 *   - US County boundaries— visible at zoom ≥ 6 (layer minzoom)
 *   - NWS CWAA / WFO      — County Warning Areas from the NOAA NWS reference
 *                           FeatureServer (fetched once per session, toggleable)
 *   - Major highways      — motorway + trunk highlight (toggleable)
 *   - Major rivers        — river + stream highlight (toggleable)
 *   - Hillshade           — Esri World Hillshade; MapLibre GL v4 has no
 *                           per-layer blend mode, so the spec's `multiply` is
 *                           emulated with desaturation + brightness clamping
 *                           so the relief darkens/clarifies the basemap and
 *                           sits *below* the weather raster where it visually
 *                           modulates how rasters read over mountain ranges.
 *
 * State/county/highway/river layers reuse the basemap's OpenMapTiles vector
 * source (no extra tile traffic); CWA is a one-shot GeoJSON fetch.
 */

import mapboxgl from 'maplibre-gl';
import { useEffect, useRef } from 'react';

import { BASE_VECTOR_SOURCE_ID, HILLSHADE_SOURCE_ID } from '@/lib/basemaps';
import type { OverlayToggles } from '@/lib/types';

import { CWAA_SOURCE_ID, HILLSHADE_LAYER_ID, NBM_LAYER_ID, OVERLAY_LAYERS } from './layerIds';
import { useMap } from './MapContainer';

/**
 * NWS County Warning Area polygons (125 features, WGS84) from the NOAA NWS
 * reference map service. `outSR=4326` returns lon/lat rings directly.
 */
const CWAA_GEOJSON_URL =
  'https://mapservices.weather.noaa.gov/static/rest/services/nws_reference_maps/nws_reference_map/FeatureServer/1/query' +
  '?where=1%3D1&outFields=cwa%2Cwfo%2Ccity%2Ccitystate%2Cstate&f=geojson&outSR=4326';

const CWAA_FETCH_TIMEOUT_MS = 12_000;

/** Session-level cache: the CWA polygons are static (updated quarterly). */
let cwaaPromise: Promise<GeoJSON.FeatureCollection> | null = null;

function fetchCwaa(): Promise<GeoJSON.FeatureCollection> {
  if (!cwaaPromise) {
    cwaaPromise = new Promise((resolve, reject) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), CWAA_FETCH_TIMEOUT_MS);
      fetch(CWAA_GEOJSON_URL, { signal: controller.signal })
        .then((res) => {
          if (!res.ok) throw new Error(`CWA fetch failed (${res.status})`);
          return res.json() as Promise<GeoJSON.FeatureCollection>;
        })
        .then((data) => {
          clearTimeout(timer);
          if (!data || data.type !== 'FeatureCollection' || !Array.isArray(data.features)) {
            throw new Error('CWA payload is not a FeatureCollection');
          }
          resolve(data);
        })
        .catch((err) => {
          clearTimeout(timer);
          cwaaPromise = null; // allow a retry next mount
          reject(err);
        });
    });
  }
  return cwaaPromise;
}

interface VectorOverlaysProps {
  toggles: OverlayToggles;
  /** Reports whether CWA data loaded (drives the toggle UI). */
  onCwaaAvailable?: (available: boolean) => void;
}

const LAYER_VISIBILITY: Record<keyof OverlayToggles, string | null> = {
  states: OVERLAY_LAYERS.states,
  counties: OVERLAY_LAYERS.counties,
  highways: OVERLAY_LAYERS.highways,
  rivers: OVERLAY_LAYERS.rivers,
  cwaa: OVERLAY_LAYERS.cwaa, // + label layer handled separately
  hillshade: HILLSHADE_LAYER_ID,
};

export function VectorOverlays({ toggles, onCwaaAvailable }: VectorOverlaysProps) {
  const map = useMap();

  const togglesRef = useRef(toggles);
  togglesRef.current = toggles;
  const cwaaStatusRef = useRef<boolean | null>(null);
  const onCwaaAvailableRef = useRef(onCwaaAvailable);
  onCwaaAvailableRef.current = onCwaaAvailable;

  const applyToggles = () => {
    const m = map;
    if (!m || !m.isStyleLoaded()) return;
    for (const [key, layerId] of Object.entries(LAYER_VISIBILITY) as [
      keyof OverlayToggles,
      string | null,
    ][]) {
      if (!layerId || !m.getLayer(layerId)) continue;
      m.setLayoutProperty(layerId, 'visibility', togglesRef.current[key] ? 'visible' : 'none');
    }
    if (m.getLayer(OVERLAY_LAYERS.cwaaLabels)) {
      m.setLayoutProperty(
        OVERLAY_LAYERS.cwaaLabels,
        'visibility',
        togglesRef.current.cwaa ? 'visible' : 'none',
      );
    }
    if (m.getLayer(OVERLAY_LAYERS.cwaaFill)) {
      m.setLayoutProperty(
        OVERLAY_LAYERS.cwaaFill,
        'visibility',
        togglesRef.current.cwaa ? 'visible' : 'none',
      );
    }
  };

  // ── Setup sources/layers, once per map instance / style load ─────────────
  useEffect(() => {
    if (!map) return;

    const reportCwaa = (available: boolean) => {
      if (cwaaStatusRef.current === available) return;
      cwaaStatusRef.current = available;
      onCwaaAvailableRef.current?.(available);
    };

    const setup = () => {
      // 1) Hillshade — inserted *below* the weather raster (above basemap).
      if (!map.getLayer(HILLSHADE_LAYER_ID)) {
        map.addLayer(
          {
            id: HILLSHADE_LAYER_ID,
            type: 'raster',
            source: HILLSHADE_SOURCE_ID,
            paint: {
              // `multiply`-emulation: grayscale + clamped brightness so the
              // relief modulates (darkens) the dark basemap instead of
              // washing it out. MapLibre v4 exposes no layer blend modes.
              'raster-saturation': -1,
              'raster-brightness-min': 0.2,
              'raster-brightness-max': 0.6,
              'raster-opacity': 0.4,
              'raster-fade-duration': 250,
            },
          },
          map.getLayer(NBM_LAYER_ID) ? NBM_LAYER_ID : undefined,
        );
      }

      // 2) US state lines — thin crisp white @ 0.4 opacity.
      if (!map.getLayer(OVERLAY_LAYERS.states)) {
        map.addLayer({
          id: OVERLAY_LAYERS.states,
          type: 'line',
          source: BASE_VECTOR_SOURCE_ID,
          'source-layer': 'boundary',
          filter: ['==', ['get', 'admin_level'], 4],
          paint: {
            'line-color': '#ffffff',
            'line-opacity': 0.4,
            'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.8, 7, 1.1, 12, 1.8],
          },
        });
      }

      // 3) US county boundaries — only at zoom ≥ 6.
      if (!map.getLayer(OVERLAY_LAYERS.counties)) {
        map.addLayer({
          id: OVERLAY_LAYERS.counties,
          type: 'line',
          source: BASE_VECTOR_SOURCE_ID,
          'source-layer': 'boundary',
          minzoom: 6,
          filter: ['==', ['get', 'admin_level'], 6],
          paint: {
            'line-color': '#ffffff',
            'line-opacity': 0.22,
            'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.5, 10, 0.9, 12, 1.4],
          },
        });
      }

      // 4) Major highways — motorway + trunk highlight.
      if (!map.getLayer(OVERLAY_LAYERS.highways)) {
        map.addLayer({
          id: OVERLAY_LAYERS.highways,
          type: 'line',
          source: BASE_VECTOR_SOURCE_ID,
          'source-layer': 'transportation',
          minzoom: 4,
          filter: [
            'all',
            ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false],
            ['in', ['get', 'class'], ['literal', ['motorway', 'trunk']]],
          ],
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: {
            'line-color': '#fbbf24',
            'line-opacity': 0.7,
            'line-width': ['interpolate', ['exponential', 1.3], ['zoom'], 4, 1, 9, 3.5, 12, 6],
          },
        });
      }

      // 5) Major rivers — river + stream highlight.
      if (!map.getLayer(OVERLAY_LAYERS.rivers)) {
        map.addLayer({
          id: OVERLAY_LAYERS.rivers,
          type: 'line',
          source: BASE_VECTOR_SOURCE_ID,
          'source-layer': 'waterway',
          minzoom: 4,
          filter: [
            'all',
            ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false],
            ['in', ['get', 'class'], ['literal', ['river', 'stream']]],
          ],
          paint: {
            'line-color': '#38bdf8',
            'line-opacity': 0.55,
            'line-width': ['interpolate', ['exponential', 1.3], ['zoom'], 4, 0.8, 9, 2.5, 12, 4.5],
          },
        });
      }

      // 6) NWS CWAA / WFO boundaries — one-shot GeoJSON.
      if (!map.getSource(CWAA_SOURCE_ID)) {
        fetchCwaa()
          .then((data) => {
            if (!map.isStyleLoaded() && !map.getLayer(HILLSHADE_LAYER_ID)) return;
            if (!map.getSource(CWAA_SOURCE_ID)) {
              map.addSource(CWAA_SOURCE_ID, {
                type: 'geojson',
                data,
                attribution: 'NWS CWAA boundaries (NOAA)',
              });
            }
            if (!map.getLayer(OVERLAY_LAYERS.cwaaFill)) {
              map.addLayer({
                id: OVERLAY_LAYERS.cwaaFill,
                type: 'fill',
                source: CWAA_SOURCE_ID,
                paint: {
                  'fill-color': '#f1f5f9',
                  'fill-opacity': 0.035,
                },
              });
            }
            if (!map.getLayer(OVERLAY_LAYERS.cwaa)) {
              map.addLayer({
                id: OVERLAY_LAYERS.cwaa,
                type: 'line',
                source: CWAA_SOURCE_ID,
                paint: {
                  'line-color': '#f1f5f9',
                  'line-opacity': 0.5,
                  'line-width': 1,
                  'line-dasharray': [4, 3],
                },
              });
            }
            if (!map.getLayer(OVERLAY_LAYERS.cwaaLabels)) {
              map.addLayer({
                id: OVERLAY_LAYERS.cwaaLabels,
                type: 'symbol',
                source: CWAA_SOURCE_ID,
                minzoom: 7,
                layout: {
                  'text-field': ['get', 'cwa'],
                  'text-font': ['Noto Sans Regular'],
                  'text-size': 10.5,
                  'text-optional': true,
                  'text-anchor': 'center',
                },
                paint: {
                  'text-color': '#f1f5f9',
                  'text-halo-color': '#0e1116',
                  'text-halo-width': 1.4,
                },
              });
            }
            reportCwaa(true);
            applyToggles();
          })
          .catch(() => reportCwaa(false));
      }

      applyToggles();
    };

    if (map.isStyleLoaded()) {
      setup();
    } else {
      map.on('style.load', setup);
      return () => {
        map.off('style.load', setup);
      };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  // ── Toggle changes ────────────────────────────────────────────────────────
  useEffect(() => {
    applyToggles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, toggles.states, toggles.counties, toggles.cwaa, toggles.highways, toggles.rivers, toggles.hillshade]);

  return null;
}
