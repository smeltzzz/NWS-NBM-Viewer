/**
 * Viewport → concrete tile URLs for the preloader.
 *
 * MapLibre raster sources request `{z}/{x}/{y}` tiles for the current view.
 * To warm the HTTP cache for an upcoming frame we replicate that covering
 * set with plain web-mercator maths using only the public map API (zoom +
 * bounds), so the preloader never pokes MapLibre internals.
 */

import { nbmTileUrl } from '@/lib/nbm';
import type { NbmDomain } from '@/lib/types';

/** Backend `tile_max_zoom` — mirrors WeatherRasterLayer's source maxzoom. */
export const TILE_MAX_ZOOM = 12;

/** Structural subset of a MapLibre map — keeps this module decoupled/GL-free. */
export interface MapLike {
  getZoom(): number;
  getBounds(): {
    getWest(): number;
    getEast(): number;
    getSouth(): number;
    getNorth(): number;
  };
}

/** Hard cap on preloaded tiles per frame (centre rows prioritised). */
const MAX_TILES_PER_FRAME = 24;

interface TileCoord {
  z: number;
  x: number;
  y: number;
}

function lngToTileX(lng: number, z: number): number {
  return ((lng + 180) / 360) * Math.pow(2, z);
}

function latToTileY(lat: number, z: number): number {
  const clamped = Math.min(Math.max(lat, -85.05112878), 85.05112878);
  const rad = (clamped * Math.PI) / 180;
  const sin = Math.sin(rad);
  return ((0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * Math.pow(2, z));
}

/**
 * Covering tile coords for a viewport at an integer zoom, shrinking the zoom
 * level until the set fits `maxTiles` (a low-zoom overview beats hammering
 * the backend with dozens of tiles per frame).
 */
export function coveringTiles(map: MapLike, maxTiles = MAX_TILES_PER_FRAME): TileCoord[] {
  const bounds = map.getBounds();
  const clamp = (v: number, max: number) => Math.min(max, Math.max(0, v));

  let z = Math.min(TILE_MAX_ZOOM, Math.max(0, Math.floor(map.getZoom())));
  for (;;) {
    const n = Math.pow(2, z);
    const x0 = clamp(Math.floor(lngToTileX(bounds.getWest(), z)), n - 1);
    const x1 = clamp(Math.floor(lngToTileX(bounds.getEast(), z)), n - 1);
    const y0 = clamp(Math.floor(latToTileY(bounds.getNorth(), z)), n - 1);
    const y1 = clamp(Math.floor(latToTileY(bounds.getSouth(), z)), n - 1);

    const cols = x1 - x0 + 1;
    const rows = y1 - y0 + 1;
    if (cols * rows <= maxTiles || z === 0) {
      const tiles: TileCoord[] = [];
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) tiles.push({ z, x, y });
      }
      return tiles;
    }
    z -= 1;
  }
}

/**
 * Concrete tile URLs for one forecast frame over the current viewport
 * (empty list when no map is available — the engine treats that as "nothing
 * to preload", never as buffering).
 */
export function frameTileUrls(
  map: MapLike | null | undefined,
  params: { domain: NbmDomain; cycle: string; element: string; fhour: number },
): string[] {
  if (!map) return [];
  const template = nbmTileUrl(params.domain, params.cycle, params.element, params.fhour);
  return coveringTiles(map).map(
    ({ z, x, y }) => template.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y)),
  );
}
