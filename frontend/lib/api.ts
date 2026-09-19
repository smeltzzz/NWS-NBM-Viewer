/**
 * Minimal typed API client. All calls go through the Next.js rewrite proxy
 * (same origin), so there are no CORS concerns in the browser.
 */

import { config } from '@/lib/config';
import type {
  HealthInfo,
  LatestRunInfo,
  LayoutResponse,
  NbmDomain,
  NbmProduct,
  ProbePointResponse,
  VersionInfo,
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
