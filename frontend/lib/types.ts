/**
 * Shared types mirroring the backend API contracts.
 */

export type NbmDomain = 'co' | 'ak' | 'hi' | 'pr' | 'gu' | 'oc';
export type NbmProduct = 'core' | 'qmd';

export interface DomainInfo {
  code: string;
  name: string;
  resolution_km: number;
  cycles_per_day: number;
  cycles: number[];
  notes: string;
  bbox: [number, number, number, number];
}

export interface ProductInfo {
  code: string;
  name: string;
  description: string;
  cycles: string;
}

export interface ColourStop {
  value: number; // normalised 0..1
  hex: string;
}

export interface ColourMap {
  label: string;
  units_hint: string;
  stops: ColourStop[];
}

export interface VersionInfo {
  service: string;
  apiVersion: string;
  appVersion: string;
  environment: string;
  apiPrefix: string;
  nbm: {
    defaultDomain: NbmDomain;
    defaultProduct: NbmProduct;
    defaultVariable: string;
    defaultForecastHour: number;
    maxForecastHour: number;
  };
}

export interface HealthInfo {
  status: 'ok' | 'degraded';
  service: string;
  version: string;
  environment?: string;
  checks?: Record<string, unknown>;
}

export interface LayoutWindow {
  start: number;
  end: number;
  step: number;
}

export interface LayoutResponse {
  domain: NbmDomain;
  domainName: string;
  resolutionKm: number;
  product: NbmProduct;
  hours: number[];
  windows: LayoutWindow[];
  maxForecastHour: number;
  isStaticApproximation: boolean;
}

/** A published NBM cycle (``GET /runs/latest``). */
export interface LatestRunInfo {
  date: string; // YYYYMMDD (UTC)
  cycle: number; // 0-23 (UTC hour)
  cycle_time?: string; // ISO-8601 UTC
  domain: NbmDomain;
  product: NbmProduct;
  available_forecast_hours: number[];
  f001_available?: boolean;
}

/** One element sampled by ``GET /probe/point``. */
export interface ProbePointValue {
  element: string;
  name: string;
  value: number | null;
  units: string;
  missing: boolean;
  formatted?: string;
  category?: string;
  raw_grib?: number | null;
}

/** ``GET /probe/point`` response. */
export interface ProbePointResponse {
  lat: number;
  lon: number;
  domain: string;
  cycle: string;
  forecast_hour: number;
  valid_time_utc: string;
  method: string;
  grid_index?: Record<string, unknown>;
  values: Record<string, ProbePointValue>;
  summary?: {
    temperature?: string | null;
    dewpoint?: string | null;
    wind?: string | null;
    wind_gust?: string | null;
    qpf?: string | null;
    sky_cover?: string | null;
    units?: string;
  };
  timings_ms?: Record<string, number>;
}

/** Which optional vector overlays are visible. */
export interface OverlayToggles {
  states: boolean;
  counties: boolean;
  cwaa: boolean;
  highways: boolean;
  rivers: boolean;
  hillshade: boolean;
}
