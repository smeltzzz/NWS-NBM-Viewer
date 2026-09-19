/**
 * Basemap style registry.
 *
 * The default "dark" style is a hand-tuned dark vector style built on
 * OpenFreeMap's key-free planetary vector tiles (OpenMapTiles schema) plus
 * Esri World Hillshade — dark, high-contrast and lean, so the NBM weather
 * rasters and the white vector overlays stand out.
 *
 * The raster styles (CARTO Dark Matter / Positron / OSM) are kept as
 * fallbacks; everything here is key-free and works out of the box.
 */

import type { ExpressionSpecification, StyleSpecification } from 'maplibre-gl';

// ── OpenFreeMap (key-free planetary vector tiles) ────────────────────────────
// The `/planet` URL is a TileJSON manifest that resolves to the current
// snapshot, so it always points at fresh tiles without hard-coding dates.
const OFM_VECTOR_URL = 'https://tiles.openfreemap.org/planet';
const OFM_SPRITE = 'https://tiles.openfreemap.org/sprites/ofm_f384/ofm';
const OFM_GLYPHS = 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf';

/** Esri World Hillshade — global relief, 1:72k (zoom 9); US down to ~1:9k.
 *  Note the ArcGIS tile order: {z}/{y}/{x}. */
const ESRI_HILLSHADE_TILES = [
  'https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}',
];

export const BASE_VECTOR_SOURCE_ID = 'openmaptiles';
export const HILLSHADE_SOURCE_ID = 'nbm-hillshade';

const OFM_ATTRIBUTION =
  '© <a href="https://openfreemap.org">OpenFreeMap</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © Esri';

const font = ['Noto Sans Regular'];

const polygonFilter: ExpressionSpecification = [
  'match',
  ['geometry-type'],
  ['MultiPolygon', 'Polygon'],
  true,
  false,
];

/**
 * Dark weather-workhorse basemap.
 *
 * Layer order (bottom → top): background → water → landcover/landuse →
 * waterway → subtle transport → country/state boundaries → place labels.
 * The overlay layers (hillshade, state/county highlights, highways, rivers,
 * CWA, NBM raster) are added at runtime above these by
 * `VectorOverlays` / `WeatherRasterLayer`.
 */
const darkVector: StyleSpecification = {
  version: 8,
  name: 'NBM Dark',
  sources: {
    [BASE_VECTOR_SOURCE_ID]: {
      type: 'vector',
      url: OFM_VECTOR_URL,
    },
    [HILLSHADE_SOURCE_ID]: {
      type: 'raster',
      tiles: ESRI_HILLSHADE_TILES,
      tileSize: 256,
      maxzoom: 9,
      attribution: 'Hillshade © Esri',
    },
  },
  sprite: OFM_SPRITE,
  glyphs: OFM_GLYPHS,
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#0e1116' },
    },
    {
      id: 'water',
      type: 'fill',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'water',
      filter: ['all', polygonFilter, ['!=', ['get', 'brunnel'], 'tunnel']],
      paint: { 'fill-color': '#15202c' },
    },
    {
      id: 'landuse-park',
      type: 'fill',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'landuse',
      minzoom: 6,
      filter: ['all', polygonFilter, ['in', ['get', 'class'], ['literal', ['park', 'wood', 'grassland']]]],
      paint: { 'fill-color': '#161f1a', 'fill-opacity': 0.55 },
    },
    {
      id: 'landcover-glacier',
      type: 'fill',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'landcover',
      maxzoom: 10,
      filter: ['all', polygonFilter, ['in', ['get', 'subclass'], ['literal', ['glacier', 'ice_shelf']]]],
      paint: { 'fill-color': '#1d2733' },
    },
    {
      id: 'waterway',
      type: 'line',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'waterway',
      filter: ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false],
      paint: { 'line-color': '#1b2a3a', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.5, 10, 1.6] },
    },
    {
      id: 'transport-subtle',
      type: 'line',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'transportation',
      minzoom: 5,
      filter: [
        'all',
        ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false],
        ['in', ['get', 'class'], ['literal', ['motorway', 'trunk', 'primary']]],
      ],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': '#242e3c',
        'line-width': ['interpolate', ['exponential', 1.3], ['zoom'], 5, 0.5, 11, 2.5],
      },
    },
    {
      id: 'boundary-country',
      type: 'line',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'boundary',
      filter: ['==', ['get', 'admin_level'], 2],
      paint: { 'line-color': '#33405352', 'line-width': 1 },
    },
    {
      id: 'boundary-state-base',
      type: 'line',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'boundary',
      filter: ['==', ['get', 'admin_level'], 4],
      paint: { 'line-color': '#334053', 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.7, 8, 1.2] },
    },
    {
      id: 'water-name',
      type: 'symbol',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'water_name',
      minzoom: 5,
      filter: ['match', ['geometry-type'], ['LineString', 'MultiLineString'], true, false],
      layout: {
        'symbol-placement': 'line',
        'symbol-spacing': 500,
        'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']],
        'text-font': [...font],
        'text-size': 11,
        'text-rotation-alignment': 'map',
      },
      paint: { 'text-color': '#4d6076', 'text-halo-color': '#0e1116', 'text-halo-width': 1 },
    },
    {
      id: 'place-city-large',
      type: 'symbol',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'place',
      minzoom: 3,
      maxzoom: 13,
      filter: ['all', ['match', ['geometry-type'], ['MultiPoint', 'Point'], true, false], ['==', ['get', 'class'], 'city'], ['<=', ['get', 'rank'], 3]],
      layout: {
        'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']],
        'text-font': [...font],
        'text-size': ['interpolate', ['linear'], ['zoom'], 3, 11, 8, 14],
        'text-transform': 'uppercase',
        'text-anchor': 'center',
      },
      paint: { 'text-color': '#aeb9c9', 'text-halo-color': '#0e1116', 'text-halo-width': 1.4 },
    },
    {
      id: 'place-city',
      type: 'symbol',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'place',
      minzoom: 5,
      maxzoom: 13,
      filter: ['all', ['match', ['geometry-type'], ['MultiPoint', 'Point'], true, false], ['==', ['get', 'class'], 'city'], ['>', ['get', 'rank'], 3]],
      layout: {
        'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']],
        'text-font': [...font],
        'text-size': 11,
        'text-transform': 'uppercase',
      },
      paint: { 'text-color': '#93a0b1', 'text-halo-color': '#0e1116', 'text-halo-width': 1.2 },
    },
    {
      id: 'place-town',
      type: 'symbol',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'place',
      minzoom: 8,
      maxzoom: 13,
      filter: ['all', ['match', ['geometry-type'], ['MultiPoint', 'Point'], true, false], ['in', ['get', 'class'], ['literal', ['town', 'village']]]],
      layout: {
        'text-field': ['coalesce', ['get', 'name_en'], ['get', 'name']],
        'text-font': [...font],
        'text-size': 10,
        'text-transform': 'uppercase',
      },
      paint: { 'text-color': '#7d8a9c', 'text-halo-color': '#0e1116', 'text-halo-width': 1.2 },
    },
    {
      id: 'building',
      type: 'fill',
      source: BASE_VECTOR_SOURCE_ID,
      'source-layer': 'building',
      minzoom: 12,
      filter: ['match', ['geometry-type'], ['MultiPolygon', 'Polygon'], true, false],
      paint: { 'fill-color': '#1a212b' },
    },
  ],
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
    },
  },
  layers: [{ id: 'light', type: 'raster', source: 'light' }],
};

/** Key-free OSM raster style (Wikimedia tiles are CC-BY-SA). */
const osmRaster: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

const REGISTRY: Record<string, StyleSpecification> = {
  dark: darkVector,
  vector: darkVector,
  light: lightRaster,
  osm: osmRaster,
};

export function basemapStyle(name?: string): StyleSpecification {
  const key = (name || 'dark').toLowerCase();
  return REGISTRY[key] ?? darkVector;
}
