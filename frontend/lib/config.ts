/**
 * Browser-visible runtime config (NEXT_PUBLIC_* values are inlined at build).
 * All values have safe fallbacks so the app boots with zero configuration.
 */

function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

export const config = {
  /** Base path for API calls. Relative by default → proxied by Next.js. */
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1',

  map: {
    center: [
      num('NEXT_PUBLIC_MAP_CENTER_LON', -97.5),
      num('NEXT_PUBLIC_MAP_CENTER_LAT', 38.5),
    ] as [number, number],
    zoom: num('NEXT_PUBLIC_MAP_DEFAULT_ZOOM', 4),
    basemap: process.env.NEXT_PUBLIC_BASEMAP || 'dark',
  },
} as const;
