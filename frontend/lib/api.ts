/**
 * Minimal typed API client. All calls go through the Next.js rewrite proxy
 * (same origin), so there are no CORS concerns in the browser.
 */

import { config } from '@/lib/config';
import type { HealthInfo, LayoutResponse, NbmDomain, NbmProduct, VersionInfo } from '@/lib/types';

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

export const api = {
  getVersion: () => request<VersionInfo>('/version'),
  getHealth: () => request<HealthInfo>('/health'),
  getDomains: () => request<{ domains: unknown[] }>('/nbm/domains'),
  getProducts: () => request<{ products: unknown[] }>('/nbm/products'),
  getLayout: (domain: NbmDomain, product: NbmProduct) =>
    request<LayoutResponse>(`/nbm/layout/${domain}/${product}?include=all`),
};
