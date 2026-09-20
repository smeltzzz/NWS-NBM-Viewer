/**
 * Minimal typed API client. All calls go through the Next.js rewrite proxy
 * (same origin), so there are no CORS concerns in the browser.
 */

import { config } from '@/lib/config';
import type {
  HealthInfo,
  LatestRunInfo,
  LayoutResponse,
  MeteogramResponse,
  NbmDomain,
  NbmProduct,
  ProbePointResponse,
  VersionInfo,
  WindFieldResponse,
} from '@/lib/types';

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${config.apiBaseUrl}${path}`;
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new ApiError(response.status, `Request to ${path} failed (${response.status})`);
  }

  return (await response.json()) as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  return `?${search.toString()}`;
}

export interface ProbePointParams {
  lat: number;
  lon: number;
  domain: NbmDomain;
  cycle: string; // YYYYMMDDHH
  fhour: number;
  elements?: string[];
  units?: 'imperial' | 'metric';
  method?: 'bilinear' | 'nearest';
  signal?: AbortSignal;
}

export const api = {
  getVersion: () => request<VersionInfo>('/version'),
  getHealth: () => request<HealthInfo>('/health'),
  getDomains: () => request<{ domains: unknown[] }>('/nbm/domains'),
  getProducts: () => request<{ products: unknown[] }>('/nbm/products'),
  getLayout: (domain: NbmDomain, product: NbmProduct) =>
    request<LayoutResponse>(`/nbm/layout/${domain}/${product}?include=all`),

  /** Latest completed NBM run for a domain/product (cycle + posted hours). */
  getLatestRun: (domain: NbmDomain, product: NbmProduct) =>
    request<LatestRunInfo>(
      `/runs/latest${query({ domain, product })}`,
    ),

  /** Full 10-day station meteogram time series for one lat/lon. */
  probeMeteogram: ({
    lat,
    lon,
    domain,
    cycle,
    units = 'imperial',
    method = 'bilinear',
    startFhour,
    endFhour,
    signal,
  }: {
    lat: number;
    lon: number;
    domain: NbmDomain;
    cycle: string;
    units?: 'imperial' | 'metric';
    method?: 'bilinear' | 'nearest';
    startFhour?: number;
    endFhour?: number;
    signal?: AbortSignal;
  }) =>
    request<MeteogramResponse>(
      `/probe/meteogram${query({
        lat: Number(lat.toFixed(4)),
        lon: Number(lon.toFixed(4)),
        domain,
        cycle,
        units,
        method,
        start_fhour: startFhour,
        end_fhour: endFhour,
      })}`,
      { signal },
    ),

  /** 10 m wind U/V vector field over a viewport lattice (particle/barb layers). */
  probeWindField: ({
    domain,
    cycle,
    fhour,
    bbox,
    cols,
    rows,
    units = 'imperial',
    signal,
  }: {
    domain: NbmDomain;
    cycle: string;
    fhour: number;
    bbox: [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]
    cols: number;
    rows: number;
    units?: 'imperial' | 'metric';
    signal?: AbortSignal;
  }) =>
    request<WindFieldResponse>(
      `/probe/wind-field${query({
        domain,
        cycle,
        fhour,
        min_lon: Number(bbox[0].toFixed(4)),
        min_lat: Number(bbox[1].toFixed(4)),
        max_lon: Number(bbox[2].toFixed(4)),
        max_lat: Number(bbox[3].toFixed(4)),
        cols,
        rows,
        units,
      })}`,
      { signal },
    ),

  /** Sample NBM elements at a single lat/lon and forecast hour. */
  probePoint: ({
    lat,
    lon,
    domain,
    cycle,
    fhour,
    elements,
    units = 'imperial',
    method = 'bilinear',
    signal,
  }: ProbePointParams) =>
    request<ProbePointResponse>(
      `/probe/point${query({
        lat: Number(lat.toFixed(4)),
        lon: Number(lon.toFixed(4)),
        domain,
        cycle,
        fhour,
        elements: elements?.length ? elements.join(',') : undefined,
        units,
        method,
      })}`,
      { signal },
    ),
};
