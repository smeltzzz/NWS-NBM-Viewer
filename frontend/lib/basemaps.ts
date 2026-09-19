/**
 * Basemap style registry.
 *
 * DEMO_FIXTURES / OSM require no API key and work out of the box; anything
 * commercial (MapTiler, Stadia) is wired in only when NEXT_PUBLIC_BASEMAP_KEY
 * is provided. Production should swap in a licensed style, but a dependency-free
 * basemap keeps the viewer bootable in any network environment.
 */

import type { StyleSpecification } from 'maplibre-gl';

/** Key-free OSM raster style (Wikimedia tiles are CC-BY-SA). */
const osmRaster: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

/** Dark raster basemap (CARTO Dark Matter, public but rate-limited). */
const darkRaster: StyleSpecification = {
  version: 8,
  sources: {
    dark: {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        'https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap © CARTO',
    },
  },
  layers: [{ id: 'dark', type: 'raster', source: 'dark' }],
};

/** Light raster basemap (CARTO Positron). */
const lightRaster: StyleSpecification = {
  version: 8,
  sources: {
    light: {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        'https://d.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap © CARTO',
    },
  },
  layers: [{ id: 'light', type: 'raster', source: 'light' }],
};

const REGISTRY: Record<string, StyleSpecification> = {
  dark: darkRaster,
  light: lightRaster,
  osm: osmRaster,
};

export function basemapStyle(name?: string): StyleSpecification {
  const key = (name || 'dark').toLowerCase();
  return REGISTRY[key] ?? darkRaster;
}
