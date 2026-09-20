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

/** How the wind quantity is displayed on the map. */
export type WindDisplayMode = 'grid' | 'particles' | 'barbs';

// ── Station meteogram (GET /probe/meteogram) ─────────────────────────────────

/** One forecast-hour point of a station meteogram (values in display units). */
export interface MeteogramPoint {
  forecast_hour: number;
  valid_time_utc: string;
  temperature: number | null;
  dewpoint: number | null;
  max_temperature: number | null;
  min_temperature: number | null;
  qpf: number | null;
  qpf_percentiles: { p10: number | null; p50: number | null; p90: number | null };
  snow: number | null;
  snow_percentiles: { p10: number | null; p50: number | null; p90: number | null };
  ice: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  wind_gust: number | null;
  sky_cover: number | null;
  ceiling_height: number | null;
  precip_type: number | null;
  precip_type_label: string | null;
  pop: number | null;
}

/** Full 10-day station meteogram payload. */
export interface MeteogramResponse {
  lat: number;
  lon: number;
  domain: string;
  cycle: string;
  start_fhour: number;
  end_fhour: number;
  forecast_hours: number[];
  units: 'imperial' | 'metric';
  unit_labels: Record<string, string>;
  series: MeteogramPoint[];
  grid_index?: Record<string, unknown>;
  method: string;
  point_count: number;
  missing_count: number;
  timings_ms?: Record<string, number>;
}

// ── Wind vector field (GET /probe/wind-field) ────────────────────────────────

/** U/V wind vector grid over a viewport lattice (row-major, row 0 = north). */
export interface WindFieldResponse {
  domain: string;
  cycle: string;
  fhour: number;
  bbox: [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]
  cols: number;
  rows: number;
  units: string; // "kt" or "m/s"
  u: (number | null)[];
  v: (number | null)[];
  timings_ms?: Record<string, number>;
}
