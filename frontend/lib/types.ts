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
