'use client';

/**
 * Bottom-left control panel: raster opacity slider (bound to the state that
 * drives `raster-opacity`), toggle chips for the vector overlays and the
 * wind-display mode selector (Standard Grid / Wind Particles / Wind Barbs).
 */

import type { OverlayToggles, WindDisplayMode } from '@/lib/types';

import { RASTER_DEFAULT_OPACITY } from './WeatherRasterLayer';

interface OverlayControlsProps {
  opacity: number;
  onOpacityChange: (opacity: number) => void;
  toggles: OverlayToggles;
  onToggle: (key: keyof OverlayToggles) => void;
  cwaaAvailable: boolean;
  windDisplay: WindDisplayMode;
  onWindDisplayChange: (mode: WindDisplayMode) => void;
  /** Whether the active product is a wind quantity (enables the group). */
  windProductActive: boolean;
}

const OVERLAY_OPTIONS: { key: keyof OverlayToggles; label: string }[] = [
  { key: 'states', label: 'States' },
  { key: 'counties', label: 'Counties' },
  { key: 'cwaa', label: 'CWA / WFO' },
  { key: 'highways', label: 'Highways' },
  { key: 'rivers', label: 'Rivers' },
  { key: 'hillshade', label: 'Hillshade' },
];

const WIND_MODES: { value: WindDisplayMode; label: string; hint: string }[] = [
  { value: 'grid', label: 'Standard Grid', hint: 'Raster tile of the selected wind element' },
  { value: 'particles', label: 'Wind Particles', hint: 'Animated particle flow advected by the 10 m U/V field' },
  { value: 'barbs', label: 'Wind Barbs', hint: 'Static meteorological barbs on the vector grid' },
];

export function OverlayControls({
  opacity,
  onOpacityChange,
  toggles,
  onToggle,
  cwaaAvailable,
  windDisplay,
  onWindDisplayChange,
  windProductActive,
}: OverlayControlsProps) {
  return (
    <div className="pointer-events-auto flex max-w-[280px] flex-col gap-2.5 rounded-lg border border-white/10 bg-surface-raised/95 p-3 shadow-panel backdrop-blur">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-white/60">
          Overlays
        </span>
        <span className="text-[10px] text-white/35">zoom ≥ 6 → counties</span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {OVERLAY_OPTIONS.map(({ key, label }) => {
          const disabled = key === 'cwaa' && !cwaaAvailable;
          const active = toggles[key];
          return (
            <button
              key={key}
              type="button"
              disabled={disabled}
              onClick={() => onToggle(key)}
              title={
                disabled
                  ? 'CWA boundary data unavailable'
                  : `${active ? 'Hide' : 'Show'} ${label.toLowerCase()}`
              }
              className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? 'border-accent/60 bg-accent/15 text-accent'
                  : 'border-white/15 bg-white/5 text-white/60 hover:border-white/30 hover:text-white/85'
              } ${disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}`}
            >
              {label}
              {disabled ? ' (n/a)' : ''}
            </button>
          );
        })}
      </div>

      {/* Wind display mode — applies when a wind product is selected. */}
      <div className={`border-t border-white/10 pt-2 ${windProductActive ? '' : 'opacity-45'}`}>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-white/50">
            Wind display
          </span>
          {!windProductActive && (
            <span className="text-[9px] text-white/40">select a wind product</span>
          )}
        </div>
        <div className="flex gap-1.5" role="radiogroup" aria-label="Wind display mode">
          {WIND_MODES.map(({ value, label, hint }) => {
            const active = windDisplay === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={!windProductActive && value !== 'grid'}
                onClick={() => onWindDisplayChange(value)}
                title={hint}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  active
                    ? 'border-accent/60 bg-accent/15 text-accent'
                    : 'border-white/15 bg-white/5 text-white/60 hover:border-white/30 hover:text-white/85'
                } ${!windProductActive && value !== 'grid' ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      <label className="flex items-center gap-2.5">
        <span className="whitespace-nowrap text-[11px] text-white/60">Raster opacity</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.02}
          value={opacity}
          onChange={(e) => onOpacityChange(Number(e.target.value))}
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded bg-white/15 accent-accent"
          aria-label="Weather raster opacity"
        />
        <span className="w-8 text-right font-mono text-[11px] text-accent">
          {opacity.toFixed(2)}
        </span>
      </label>

      <p className="text-[10px] leading-4 text-white/35">
        Default {RASTER_DEFAULT_OPACITY.toFixed(2)} — hover the map for live
        values, click to open the station meteogram.
      </p>
    </div>
  );
}
