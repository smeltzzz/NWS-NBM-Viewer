/**
 * Client-side mirror of the backend's NBM catalogue.
 *
 * This is the *default* data the UI renders before the async catalogue fetch
 * resolves; it is also the source for the legend. The backend is authoritative
 * and the computed `tileUrl` points at the demo tile source until the GRIB
 * materialiser lands.
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

/** Curated core elements for the first UX slice. */
export const ELEMENTS: { key: string; label: string; units: string }[] = [
  { key: 'tmp', label: 'Temperature', units: '°C' },
  { key: 'mint', label: 'Min temperature', units: '°C' },
  { key: 'maxt', label: 'Max temperature', units: '°C' },
  { key: 'qpf06', label: '6-h precipitation', units: 'mm' },
  { key: 'pop12', label: '12-h PoP', units: '%' },
  { key: 'wind', label: 'Wind speed', units: 'm/s' },
  { key: 'gust', label: 'Wind gust', units: 'm/s' },
  { key: 'rh', label: 'Relative humidity', units: '%' },
  { key: 'sky', label: 'Sky cover', units: '%' },
  { key: 'mslp', label: 'MSLP', units: 'hPa' },
];

/** Colour ramps agreed with the backend `app/colormaps.py`. */
export const COLOUR_MAPS: Record<string, ColourMap> = {
  nbm_temp: {
    label: 'Temperature',
    units_hint: '°C',
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
    units_hint: 'mm',
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
    units_hint: 'm/s',
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
    mint: 'nbm_temp',
    maxt: 'nbm_temp',
    qpf01: 'nbm_precip',
    qpf06: 'nbm_precip',
    pop01: 'nbm_pop',
    pop06: 'nbm_pop',
    pop12: 'nbm_pop',
    wind: 'nbm_wind',
    gust: 'nbm_wind',
    rh: 'nbm_rh',
    sky: 'nbm_pop',
    mslp: 'nbm_wind',
  };
  return map[element] ?? 'nbm_temp';
}

/**
 * Demo XYZ tile URL. Swapped for the real NBM tile endpoint once the data
 * pipeline lands; the query contract (colormap name) is stable.
 */
export function tileUrl(
  variable: string,
  forecastHour: number,
  _product: NbmProduct = 'core',
  _domain: NbmDomain = 'co',
): string {
  const cmap = colormapForElement(variable);
  return `${config.apiBaseUrl}/tiles/demo/{z}/{x}/{y}.png?colormap=${cmap}&variable=${variable}&f=${forecastHour}`;
}
