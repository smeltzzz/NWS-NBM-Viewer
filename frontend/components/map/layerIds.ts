/**
 * Shared MapLibre source/layer ids.
 *
 * Keeping them in one place lets the independent overlay components
 * (weather raster, vector overlays, probe readout) reference each other's
 * layers to control stacking order without importing each other.
 *
 * Stack order (bottom → top):
 *   basemap layers
 *   <nbm-hillshade>        terrain relief (multiply-emulated)
 *   <nbm-raster>           active NBM forecast raster
 *   <nbm-state-borders>    US state lines (white, 0.4)
 *   <nbm-county-borders>   US county boundaries (zoom ≥ 6)
 *   <nbm-highways>         major highways (toggle)
 *   <nbm-rivers>           major rivers (toggle)
 *   <nbm-cwaa>             NWS County Warning Areas (toggle)
 *   <nbm-cwaa-labels>      CWA codes
 */

export const NBM_SOURCE_ID = 'nbm-raster';
export const NBM_LAYER_ID = 'nbm-raster';

export const HILLSHADE_LAYER_ID = 'nbm-hillshade';

export const OVERLAY_LAYERS = {
  states: 'nbm-state-borders',
  counties: 'nbm-county-borders',
  highways: 'nbm-highways',
  rivers: 'nbm-rivers',
  cwaa: 'nbm-cwaa',
  cwaaFill: 'nbm-cwaa-fill',
  cwaaLabels: 'nbm-cwaa-labels',
} as const;

export const CWAA_SOURCE_ID = 'nbm-cwaa';
