'use client';

/**
 * Unit system handling for meteorological workstation.
 * Supports Imperial (°F, in, mph, kts) vs Metric (°C, mm, km/h, m/s)
 * Conversion helpers + formatting.
 */

export type UnitSystem = 'imperial' | 'metric';

export interface UnitLabels {
  temp: string;       // °F / °C
  precip: string;     // in / mm
  wind: string;       // mph / km/h  (primary)
  windAlt: string;    // kts / m/s   (secondary for aviation/marine)
  distance: string;   // mi / km
  pressure: string;   // inHg / hPa (not toggled but useful)
}

export const UNIT_LABELS: Record<UnitSystem, UnitLabels> = {
  imperial: {
    temp: '°F',
    precip: 'in',
    wind: 'mph',
    windAlt: 'kts',
    distance: 'mi',
    pressure: 'inHg',
  },
  metric: {
    temp: '°C',
    precip: 'mm',
    wind: 'km/h',
    windAlt: 'm/s',
    distance: 'km',
    pressure: 'hPa',
  },
};

// ── Conversions ───────────────────────────────────────────────────────────
export function cToF(c: number): number { return c * 9/5 + 32; }
export function fToC(f: number): number { return (f - 32) * 5/9; }

export function mmToIn(mm: number): number { return mm / 25.4; }
export function inToMm(inch: number): number { return inch * 25.4; }

export function msToMph(ms: number): number { return ms * 2.23694; }
export function msToKts(ms: number): number { return ms * 1.94384; }
export function msToKmh(ms: number): number { return ms * 3.6; }

export function mphToMs(mph: number): number { return mph / 2.23694; }
export function ktsToMs(kts: number): number { return kts / 1.94384; }

export function hPaToInHg(hpa: number): number { return hpa * 0.0295299830714; }

// ── Formatting with unit system ───────────────────────────────────────────
export function formatTemp(valueC: number, system: UnitSystem, decimals = 0): string {
  if (system === 'imperial') {
    return `${cToF(valueC).toFixed(decimals)}°F`;
  }
  return `${valueC.toFixed(decimals)}°C`;
}

export function formatPrecip(valueMm: number, system: UnitSystem, decimals = 2): string {
  if (system === 'imperial') {
    return `${mmToIn(valueMm).toFixed(decimals)} in`;
  }
  return `${valueMm.toFixed(decimals)} mm`;
}

export function formatWind(valueMs: number, system: UnitSystem, useAlt = false): string {
  if (system === 'imperial') {
    return useAlt ? `${msToKts(valueMs).toFixed(0)} kts` : `${msToMph(valueMs).toFixed(0)} mph`;
  }
  return useAlt ? `${valueMs.toFixed(1)} m/s` : `${msToKmh(valueMs).toFixed(0)} km/h`;
}

// Generic value conversion for colormap tick labels.
// element: NBM element key, value: in backend native units (degC, mm, m/s, etc)
export function convertValueForDisplay(
  value: number,
  element: string,
  system: UnitSystem,
): number {
  const el = element.toLowerCase();
  // Temperature family (degC native)
  if (['tmp','maxt','mint','tdp','appt','dpt','pmaxt','pmint'].some(k => el.includes(k))) {
    return system === 'imperial' ? cToF(value) : value;
  }
  // Precip family (mm native)
  if (['qpf','snow','pqpf','qmd_qpf'].some(k => el.includes(k))) {
    return system === 'imperial' ? mmToIn(value) : value;
  }
  // Wind family (m/s native)
  if (['wind','gust','probgust','probwind'].some(k => el.includes(k))) {
    if (system === 'imperial') return msToMph(value);
    return msToKmh(value); // primary metric is km/h, but we convert consistently
  }
  // Pressure Pa -> hPa native? backend says Pa, but we want hPa
  if (['mslp','pmslp'].some(k => el.includes(k))) {
    // value in Pa, convert to hPa
    const hpa = value / 100;
    return system === 'imperial' ? hPaToInHg(hpa) : hpa;
  }
  return value;
}

export function getDisplayUnit(element: string, system: UnitSystem, useAltWind = false): string {
  const labels = UNIT_LABELS[system];
  const el = element.toLowerCase();
  if (['tmp','maxt','mint','tdp','appt','dpt','pmaxt','pmint'].some(k => el.includes(k))) return labels.temp;
  if (['qpf','snow','pqpf','qmd_qpf','pop','probqpf'].some(k => el.includes(k))) {
    // PoP is percent, not precip depth, but threshold probs are still precip units in label
    if (el.includes('pop') || el.includes('prob')) {
      // For threshold exceedance, the threshold unit is precip, but value is %
      // We'll return % for probability products
      if (el.startsWith('pop') || el.startsWith('prob') || el.startsWith('qmd_pop')) return '%';
    }
    return labels.precip;
  }
  if (['wind','gust'].some(k => el.includes(k))) {
    return useAltWind ? labels.windAlt : labels.wind;
  }
  if (['mslp','pmslp'].some(k => el.includes(k))) return labels.pressure;
  if (['rh','sky','snowc','dur'].some(k => el.includes(k))) return '%';
  if (el.includes('cape')) return 'J/kg';
  if (el.includes('refc')) return 'dBZ';
  return labels.temp; // fallback
}
