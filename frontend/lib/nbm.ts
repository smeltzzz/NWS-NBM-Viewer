/**
 * Client-side mirror of the backend's NBM catalogue.
 *
 * This is the *default* data the UI renders before the async catalogue fetch
 * resolves; it is also the source for the legend and for the per-domain
 * camera presets. The backend is authoritative for what is actually served.
 */

import { config } from '@/lib/config';
import type { ColourMap, NbmDomain, NbmProduct } from '@/lib/types';

export const DOMAINS: { code: NbmDomain; label: string; resolution: string }[] = [
  { code: 'co', label: 'CONUS', resolution: '2.5 km' },
  { code: 'ak', label: 'Alaska', resolution: '3 km' },
  { code: 'hi', label: 'Hawaii', resolution: '2.5 km' },
  { code: 'pr', label: 'Puerto Rico', resolution: '1.25 km' },
  { code: 'gu', label: 'Guam', resolution: '2.5 km' },
  { code: 'oc', label: 'Oceanic', resolution: '10 km' },
];

export const PRODUCTS: { code: NbmProduct; label: string }[] = [
  { code: 'core', label: 'Core / deterministic' },
  { code: 'qmd', label: 'QMD (calibrated)' },
];

/**
 * Camera viewport preset for each NBM domain: `[lon, lat]` center + zoom.
 * These are the home positions used when the user switches domains.
 */
export const DOMAIN_VIEWPORTS: Record<
  NbmDomain,
  { center: [number, number]; zoom: number }
> = {
  co: { center: [-98.5795, 39.8283], zoom: 4 },
  ak: { center: [-152.0, 64.0], zoom: 3.5 },
  hi: { center: [-157.5, 20.5], zoom: 6.5 },
  pr: { center: [-66.5, 18.2], zoom: 7.5 },
  gu: { center: [144.75, 13.44], zoom: 8 },
  oc: { center: [-140.0, 30.0], zoom: 2.5 },
};

/** Curated core elements — keys match the backend catalog element codes. */
export const ELEMENTS: { key: string; label: string; units: string }[] = [
  { key: 'tmp', label: 'Temperature', units: '°F' },
  { key: 'max', label: 'Max temperature', units: '°F' },
  { key: 'min', label: 'Min temperature', units: '°F' },
  { key: 'dpt', label: 'Dewpoint', units: '°F' },
  { key: 'rh', label: 'Relative humidity', units: '%' },
  { key: 'qpf_1h', label: 'QPF (1 h)', units: 'in' },
  { key: 'qpf_6h', label: 'QPF (6 h)', units: 'in' },
  { key: 'qpf_12h', label: 'QPF (12 h)', units: 'in' },
  { key: 'qpf_24h', label: 'QPF (24 h)', units: 'in' },
  { key: 'pop12', label: 'PoP (12 h)', units: '%' },
  { key: 'pop06', label: 'PoP (6 h)', units: '%' },
  { key: 'wind', label: 'Wind speed', units: 'kt' },
  { key: 'gust', label: 'Wind gust', units: 'kt' },
  { key: 'sky', label: 'Sky cover', units: '%' },
  { key: 'refc', label: 'Composite reflectivity', units: 'dBZ' },
  { key: 'sbcape', label: 'Surface-based CAPE', units: 'J/kg' },
];

/** Colour ramps agreed with the backend `app/colormaps.py`. */
export const COLOUR_MAPS: Record<string, ColourMap> = {
  nbm_temp: {
    label: 'Temperature',
    units_hint: '°F',
    stops: [
      { value: 0.0, hex: '#5E4FA2' },
      { value: 0.2, hex: '#3288BD' },
      { value: 0.45, hex: '#ABDDA4' },
      { value: 0.6, hex: '#FEE08B' },
      { value: 0.8, hex: '#FDAE61' },
      { value: 1.0, hex: '#D53E4F' },
    ],
  },
  nbm_precip: {
    label: 'Precipitation',
    units_hint: 'in',
    stops: [
      { value: 0.0, hex: '#EAF7FF' },
      { value: 0.2, hex: '#A6E3A1' },
      { value: 0.4, hex: '#38BC5C' },
      { value: 0.6, hex: '#FFE14A' },
      { value: 0.8, hex: '#FF7B00' },
      { value: 1.0, hex: '#7A0177' },
    ],
  },
  nbm_wind: {
    label: 'Wind speed',
    units_hint: 'kt',
    stops: [
      { value: 0.0, hex: '#FFFFFF' },
      { value: 0.2, hex: '#BEE8FF' },
      { value: 0.4, hex: '#6DD3CE' },
      { value: 0.6, hex: '#FFE14A' },
      { value: 0.8, hex: '#FF7B00' },
      { value: 1.0, hex: '#B02A63' },
    ],
  },
  nbm_rh: {
    label: 'Relative humidity',
    units_hint: '%',
    stops: [
      { value: 0.0, hex: '#A6611A' },
      { value: 0.35, hex: '#DFC27D' },
      { value: 0.6, hex: '#F5F5BD' },
      { value: 0.8, hex: '#80CDC1' },
      { value: 1.0, hex: '#045A8D' },
    ],
  },
  nbm_pop: {
    label: 'Probability of precipitation',
    units_hint: '%',
    stops: [
      { value: 0.0, hex: '#EAF2FB' },
      { value: 0.25, hex: '#B2DF8A' },
      { value: 0.5, hex: '#33A02C' },
      { value: 0.7, hex: '#FDBF6F' },
      { value: 0.85, hex: '#F58025' },
      { value: 1.0, hex: '#CB181D' },
    ],
  },
};

/** Map an element key to a colour ramp (most-common associations). */
export function colormapForElement(element: string): string {
  const map: Record<string, string> = {
    tmp: 'nbm_temp',
    max: 'nbm_temp',
    min: 'nbm_temp',
    dpt: 'nbm_temp',
    qpf_1h: 'nbm_precip',
    qpf_6h: 'nbm_precip',
    qpf_12h: 'nbm_precip',
    qpf_24h: 'nbm_precip',
    pop12: 'nbm_pop',
    pop06: 'nbm_pop',
    pop01: 'nbm_pop',
    wind: 'nbm_wind',
    gust: 'nbm_wind',
    rh: 'nbm_rh',
    sky: 'nbm_pop',
    refc: 'nbm_wind',
    sbcape: 'nbm_precip',
  };
  return map[element] ?? 'nbm_temp';
}

/**
 * NBM WebP tile URL template for MapLibre raster sources.
 *
 * ``/api/v1/tiles/{domain}/{cycle}/{element}/{fhour}/{z}/{x}/{y}.webp``
 *
 * ``empty=image`` makes out-of-domain tiles a transparent 1×1 WebP instead
 * of a 204, which MapLibre raster sources handle more gracefully.
 */
export function nbmTileUrl(
  domain: NbmDomain,
  cycle: string,
  element: string,
  fhour: number,
): string {
  return `${config.apiBaseUrl}/tiles/${domain}/${cycle}/${element}/${fhour}/{z}/{x}/{y}.webp?empty=image`;
}

/** Standard NBM core forecast hours (fallback when run discovery fails). */
export function defaultForecastHours(): number[] {
  return [1, 6, 12, 18, 24, 36, 48, 72, 96, 120, 144, 168, 192, 216, 240, 264];
}

/**
 * Best-effort "latest cycle" when run discovery is unreachable: NBM core
 * publishes every hour; a cycle is typically complete ~20-40 min after its
 * hour, so step back one hour until 40 min have passed.
 */
export function computeFallbackCycle(now: Date = new Date()): string {
  const d = new Date(now);
  let hour = d.getUTCHours();
  if (d.getUTCMinutes() < 40) hour = (hour + 23) % 24;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}${pad(hour)}`;
}
