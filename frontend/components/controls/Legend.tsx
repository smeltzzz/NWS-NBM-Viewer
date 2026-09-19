'use client';

/**
 * Dynamic Legend & Colormap Display
 * Floating, collapsible, horizontal or vertical colorbar docked at screen corner.
 *
 * Features:
 * - Exact colormap color gradient with calibrated tick labels matching selected units
 * - Highlights current hovered value on the colorbar scale with an indicator tick
 * - Element title, valid accumulation window, and official source note
 * - Opacity slider integrated into legend container
 * - Collapsible + orientation toggle (horizontal / vertical)
 * - Responsive: bottom sheet on mobile
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cssGradient } from '@/lib/format';
import { COLOUR_MAPS } from '@/lib/nbm';
import type { NBMProductDef } from './catalog';
import type { UnitSystem } from '@/lib/units';
import { convertValueForDisplay, getDisplayUnit } from '@/lib/units';

type Orientation = 'horizontal' | 'vertical';

interface LegendProps {
  product?: NBMProductDef;
  // Fallback when product not provided (legacy)
  variable?: string;
  unitSystem?: UnitSystem;
  opacity?: number;
  onOpacityChange?: (opacity: number) => void;
  forecastHour?: number;
  cycle?: string;
  validTime?: string;
  hoveredValue?: number | null; // native units value (e.g., degC, mm, m/s)
  orientation?: Orientation;
  defaultCollapsed?: boolean;
  className?: string;
  // For external hover sync: when user hovers over legend gradient, callback with value
  onHoverValue?: (value: number | null) => void;
}

// Define value ranges for each colormap/element (native units)
// These are calibrated to NBM operational ranges
function getRangeForProduct(product?: NBMProductDef, variable?: string): { min: number; max: number; ticks: number[] } {
  const key = product?.id ?? variable ?? 'tmp';

  // Temperature family (degC)
  if (['tmp','maxt','mint','tdp','appt','dpt','pmaxt','pmint','fire_tmp','fire_rh'].includes(key) || ['tmp','maxt','mint','tdp','appt'].includes(variable ?? '')) {
    if (key === 'maxt' || key === 'fire_tmp') return { min: -20, max: 50, ticks: [-20, -10, 0, 10, 20, 30, 40, 50] };
    if (key === 'mint') return { min: -40, max: 30, ticks: [-40, -30, -20, -10, 0, 10, 20, 30] };
    return { min: -30, max: 45, ticks: [-30, -20, -10, 0, 10, 20, 30, 40] };
  }

  // Precip family (mm)
  if (['qpf01','qpf06','qpf12','qpf24','pqpf','qmd_qpf06','qmd_qpf24','snow','ice_accum'].some(k => key.includes(k)) || ['qpf','qpf01','qpf06','qpf12','qpf24','snow'].includes(variable ?? '')) {
    if (key.includes('qpf01') || key === 'qpf01') return { min: 0, max: 25, ticks: [0, 2.5, 5, 10, 15, 25] };
    if (key.includes('qpf06')) return { min: 0, max: 50, ticks: [0, 5, 10, 20, 35, 50] };
    if (key.includes('qpf24') || key.includes('snow')) return { min: 0, max: 100, ticks: [0, 10, 25, 50, 75, 100] };
    return { min: 0, max: 50, ticks: [0, 5, 10, 20, 30, 50] };
  }

  // PoP / probability (%)
  if (['pop06','pop12','prob','lightning','probgust','probwind','qmd_pop'].some(k => key.includes(k)) || ['pop12','pop06'].includes(variable ?? '')) {
    return { min: 0, max: 100, ticks: [0, 10, 30, 50, 70, 90, 100] };
  }

  // Wind family (m/s)
  if (['wind','gust','fire_wind','fire_gust','winddir'].some(k => key.includes(k)) || ['wind','gust'].includes(variable ?? '')) {
    if (key === 'winddir') return { min: 0, max: 360, ticks: [0, 90, 180, 270, 360] };
    return { min: 0, max: 35, ticks: [0, 5, 10, 15, 20, 25, 35] };
  }

  // RH
  if (['rh','maxrh','minrh','snowc'].some(k => key.includes(k)) || ['rh'].includes(variable ?? '')) {
    return { min: 0, max: 100, ticks: [0, 20, 40, 60, 80, 100] };
  }

  // CAPE
  if (key.includes('cape') || variable === 'sbcape') {
    return { min: 0, max: 4000, ticks: [0, 500, 1000, 2000, 3000, 4000] };
  }

  // Reflectivity
  if (key.includes('refc') || variable === 'refc') {
    return { min: -10, max: 70, ticks: [-10, 0, 20, 40, 60, 70] };
  }

  // Visibility, ceiling etc.
  if (key.includes('vis')) return { min: 0, max: 10000, ticks: [0, 1600, 4800, 8000, 10000] };
  if (key.includes('ceil') || key.includes('snowlev')) return { min: 0, max: 5000, ticks: [0, 500, 1000, 2000, 3500, 5000] };
  if (key.includes('mslp') || key.includes('pmslp') || variable === 'mslp') {
    return { min: 95000, max: 105000, ticks: [95000, 97000, 99000, 101000, 103000, 105000] };
  }

  // Default
  return { min: -30, max: 45, ticks: [-30, -10, 0, 15, 30, 45] };
}

function formatTickValue(nativeValue: number, product?: NBMProductDef, variable?: string, unitSystem?: UnitSystem): string {
  const el = product?.element ?? variable ?? 'tmp';
  const sys = unitSystem ?? 'imperial';
  const displayVal = convertValueForDisplay(nativeValue, el, sys);
  // Special formatting for some ranges
  if (el.includes('mslp') || el.includes('pmslp')) {
    // Pressure already converted
    return sys === 'imperial' ? displayVal.toFixed(2) : displayVal.toFixed(0);
  }
  if (Math.abs(displayVal) >= 100) return displayVal.toFixed(0);
  if (Math.abs(displayVal) >= 10) return displayVal.toFixed(1);
  return displayVal.toFixed(1);
}

export function Legend({
  product,
  variable,
  unitSystem = 'imperial',
  opacity = 0.85,
  onOpacityChange,
  forecastHour = 24,
  cycle,
  validTime,
  hoveredValue = null,
  orientation: initialOrientation = 'horizontal',
  defaultCollapsed = false,
  className = '',
  onHoverValue,
}: LegendProps) {
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);
  const [orientation, setOrientation] = useState<Orientation>(initialOrientation);
  const [localHover, setLocalHover] = useState<number | null>(null); // normalized 0-1
  const [localHoverValue, setLocalHoverValue] = useState<number | null>(null);
  const gradientRef = useRef<HTMLDivElement>(null);

  // Determine active product / element
  const elementKey = product?.element ?? variable ?? 'tmp';
  const colormapKey = product?.colormap ?? (() => {
    // fallback mapping
    const map: Record<string, string> = {
      tmp: 'nbm_temp', maxt: 'nbm_temp', mint: 'nbm_temp', tdp: 'nbm_temp', appt: 'nbm_temp',
      qpf01: 'nbm_precip', qpf06: 'nbm_precip', qpf12: 'nbm_precip', qpf24: 'nbm_precip',
      snow: 'nbm_precip', qmd_qpf06: 'nbm_precip', qmd_qpf24: 'nbm_precip',
      pop06: 'nbm_pop', pop12: 'nbm_pop', probwind: 'nbm_pop', probgust: 'nbm_pop',
      wind: 'nbm_wind', gust: 'nbm_wind', winddir: 'nbm_wind', refc: 'nbm_wind',
      rh: 'nbm_rh', maxrh: 'nbm_rh', minrh: 'nbm_rh', sky: 'nbm_pop',
      sbcape: 'nbm_precip', mslp: 'nbm_temp', pmslp: 'nbm_temp',
    };
    return map[elementKey] ?? 'nbm_temp';
  })();

  const cmap = COLOUR_MAPS[colormapKey];
  const range = useMemo(() => getRangeForProduct(product, variable), [product, variable]);

  // Compute hovered normalized position from hoveredValue
  const hoveredNorm = useMemo(() => {
    if (hoveredValue === null || hoveredValue === undefined) return null;
    const { min, max } = range;
    if (max === min) return null;
    const norm = (hoveredValue - min) / (max - min);
    return Math.max(0, Math.min(1, norm));
  }, [hoveredValue, range]);

  // Effective hover: external hoveredValue takes precedence, else local hover
  const effectiveHoverNorm = hoveredNorm ?? localHover;
  const effectiveHoverValue = hoveredValue ?? localHoverValue;

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!gradientRef.current) return;
    const rect = gradientRef.current.getBoundingClientRect();
    let norm: number;
    if (orientation === 'horizontal') {
      norm = (e.clientX - rect.left) / rect.width;
    } else {
      norm = 1 - (e.clientY - rect.top) / rect.height; // vertical bottom->top
    }
    norm = Math.max(0, Math.min(1, norm));
    const nativeValue = range.min + norm * (range.max - range.min);
    setLocalHover(norm);
    setLocalHoverValue(nativeValue);
    onHoverValue?.(nativeValue);
  }, [orientation, range, onHoverValue]);

  const handleMouseLeave = useCallback(() => {
    setLocalHover(null);
    setLocalHoverValue(null);
    onHoverValue?.(null);
  }, [onHoverValue]);

  // Touch handling for mobile
  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!gradientRef.current || !e.touches[0]) return;
    const rect = gradientRef.current.getBoundingClientRect();
    const touch = e.touches[0];
    let norm: number;
    if (orientation === 'horizontal') {
      norm = (touch.clientX - rect.left) / rect.width;
    } else {
      norm = 1 - (touch.clientY - rect.top) / rect.height;
    }
    norm = Math.max(0, Math.min(1, norm));
    const nativeValue = range.min + norm * (range.max - range.min);
    setLocalHover(norm);
    setLocalHoverValue(nativeValue);
    onHoverValue?.(nativeValue);
  }, [orientation, range, onHoverValue]);

  const displayUnit = useMemo(() => getDisplayUnit(elementKey, unitSystem), [elementKey, unitSystem]);

  const title = useMemo(() => {
    if (product) {
      const accum = product.defaultAccum ? ` ${product.defaultAccum}` : '';
      const perc = product.defaultPercentile ? ` (${product.defaultPercentile}th Percentile)` : '';
      return `${product.label}${accum}${perc}`;
    }
    if (variable) {
      return `${variable.toUpperCase()} • ${cmap?.label ?? 'NBM'}`;
    }
    return 'NBM Product';
  }, [product, variable, cmap]);

  const sourceNote = useMemo(() => {
    const v = product?.versionNote ?? 'NBM v4.2';
    const accumWindow = forecastHour ? `${forecastHour}h` : '';
    const cycleNote = cycle ? `Cycle ${cycle}` : '';
    return `${v} ${accumWindow ? `${accumWindow} Forecast` : ''} ${cycleNote} • ${validTime ? `Valid ${validTime}` : 'NCEP/NWS'}`.trim();
  }, [product, forecastHour, cycle, validTime]);

  if (!cmap) return null;

  const gradientStyle = {
    background: cssGradient(cmap.stops, orientation === 'horizontal' ? 90 : 0),
  };

  return (
    <div
      className={`
        pointer-events-auto relative flex select-none flex-col rounded-xl border border-white/10 bg-[#0c1424]/95 shadow-[0_8px_32px_rgba(0,0,0,0.6)] backdrop-blur-xl
        ${isCollapsed ? 'p-2' : 'p-3'}
        ${orientation === 'horizontal' ? 'w-[320px] md:w-[380px]' : 'w-[140px] md:w-[160px]'}
        ${className}
      `}
    >
      {/* Header */}
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[11px] font-bold uppercase tracking-wide text-white/90">
              {isCollapsed ? (product?.shortLabel ?? variable?.toUpperCase() ?? 'NBM') : title}
            </span>
            {!isCollapsed && (
              <span className="rounded bg-white/10 px-1 py-0 text-[8px] font-mono tracking-wide text-white/50">
                {displayUnit}
              </span>
            )}
          </div>
          {!isCollapsed && (
            <span className="mt-0.5 line-clamp-2 text-[10px] leading-3 text-white/40">
              {sourceNote}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {!isCollapsed && (
            <>
              <button
                type="button"
                onClick={() => setOrientation(o => (o === 'horizontal' ? 'vertical' : 'horizontal'))}
                className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-white/10 bg-white/5 text-white/40 transition hover:bg-white/10 hover:text-white/80"
                title={`Switch to ${orientation === 'horizontal' ? 'vertical' : 'horizontal'} layout`}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  {orientation === 'horizontal' ? (
                    <path d="M12 3v18M3 12h18" />
                  ) : (
                    <path d="M8 3H5a2 2 0 00-2 2v14a2 2 0 002 2h3M16 3h3a2 2 0 012 2v14a2 2 0 01-2 2h-3M8 3h8v18H8z" />
                  )}
                </svg>
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setIsCollapsed(c => !c)}
            className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-white/10 bg-white/5 text-white/40 transition hover:bg-white/10 hover:text-white/80"
            title={isCollapsed ? 'Expand legend' : 'Collapse legend'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d={isCollapsed ? 'M6 9l6 6 6-6' : 'M18 15l-6-6-6 6'} />
            </svg>
          </button>
        </div>
      </div>

      {!isCollapsed && (
        <>
          {/* Colorbar */}
          <div className={`relative ${orientation === 'horizontal' ? 'h-10 w-full' : 'flex h-[220px] w-full gap-2'}`}>
            {/* Gradient */}
            <div
              ref={gradientRef}
              className={`relative cursor-crosshair overflow-hidden rounded-md border border-white/10 ${
                orientation === 'horizontal' ? 'h-4 w-full' : 'h-full w-6'
              }`}
              style={gradientStyle}
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleMouseLeave}
              role="img"
              aria-label={`${cmap.label} color scale from ${range.min} to ${range.max}`}
            >
              {/* Hover indicator tick */}
              {effectiveHoverNorm !== null && (
                <div
                  className="absolute z-10"
                  style={
                    orientation === 'horizontal'
                      ? { left: `${effectiveHoverNorm * 100}%`, top: 0, bottom: 0 }
                      : { bottom: `${effectiveHoverNorm * 100}%`, left: 0, right: 0 }
                  }
                >
                  <div
                    className={`bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.5),0_0_8px_rgba(255,255,255,0.8)] ${
                      orientation === 'horizontal' ? 'h-full w-0.5 -translate-x-1/2' : 'h-0.5 w-full -translate-y-1/2'
                    }`}
                  />
                </div>
              )}
            </div>

            {/* Ticks */}
            <div className={orientation === 'horizontal' ? 'relative mt-1 h-4 w-full' : 'relative flex-1'}>
              {range.ticks.map(tick => {
                const norm = (tick - range.min) / (range.max - range.min);
                if (orientation === 'horizontal') {
                  return (
                    <div
                      key={tick}
                      className="absolute top-0 flex flex-col items-center"
                      style={{ left: `${norm * 100}%`, transform: 'translateX(-50%)' }}
                    >
                      <div className="h-1 w-px bg-white/30" />
                      <span className="mt-0.5 font-mono text-[9px] text-white/50">
                        {formatTickValue(tick, product, variable, unitSystem)}
                      </span>
                    </div>
                  );
                } else {
                  return (
                    <div
                      key={tick}
                      className="absolute left-0 flex items-center gap-1"
                      style={{ bottom: `${norm * 100}%`, transform: 'translateY(50%)' }}
                    >
                      <div className="h-px w-1 bg-white/30" />
                      <span className="font-mono text-[9px] text-white/50">
                        {formatTickValue(tick, product, variable, unitSystem)}
                      </span>
                    </div>
                  );
                }
              })}
            </div>

            {/* Hovered value tooltip */}
            {effectiveHoverNorm !== null && effectiveHoverValue !== null && (
              <div
                className="pointer-events-none absolute z-20 rounded-md border border-white/20 bg-[#0a1020] px-2 py-1 shadow-lg"
                style={
                  orientation === 'horizontal'
                    ? {
                        left: `${Math.max(5, Math.min(95, effectiveHoverNorm * 100))}%`,
                        top: '-28px',
                        transform: 'translateX(-50%)',
                      }
                    : {
                        bottom: `${effectiveHoverNorm * 100}%`,
                        left: '28px',
                        transform: 'translateY(50%)',
                      }
                }
              >
                <div className="whitespace-nowrap font-mono text-[10px] font-semibold text-white">
                  {formatTickValue(effectiveHoverValue, product, variable, unitSystem)} {displayUnit}
                </div>
              </div>
            )}
          </div>

          {/* Current hovered / probed value highlight */}
          {hoveredValue !== null && (
            <div className="mt-2 flex items-center gap-2 rounded-md border border-sky-500/20 bg-sky-500/10 px-2 py-1">
              <div className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
              <span className="text-[10px] font-medium text-sky-200">Probed:</span>
              <span className="font-mono text-[11px] font-bold text-white">
                {formatTickValue(hoveredValue, product, variable, unitSystem)} {displayUnit}
              </span>
              <div className="ml-auto h-1.5 w-16 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full bg-sky-400 transition-all"
                  style={{ width: `${(effectiveHoverNorm ?? 0) * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Opacity slider integrated */}
          <div className="mt-3 flex items-center gap-2 rounded-md border border-white/5 bg-white/[0.02] px-2.5 py-2">
            <span className="text-[10px] font-medium uppercase tracking-wide text-white/40">Opacity</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.02}
              value={opacity}
              onChange={e => onOpacityChange?.(Number(e.target.value))}
              className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-white/10 accent-sky-400"
              aria-label="Raster opacity"
            />
            <span className="w-8 text-right font-mono text-[10px] text-sky-300">{Math.round(opacity * 100)}%</span>
          </div>

          {/* Source note full */}
          <div className="mt-2 flex items-center justify-between border-t border-white/5 pt-2">
            <span className="text-[9px] font-mono text-white/25">
              {product ? `${product.versionNote ?? 'NBM v4.2'} • ${product.element}` : `Colormap: ${colormapKey}`}
            </span>
            <span className="text-[9px] text-white/20">Calibrated • {unitSystem}</span>
          </div>
        </>
      )}

      {/* Collapsed mini bar */}
      {isCollapsed && (
        <div className="flex items-center gap-2">
          <div className="h-1.5 flex-1 rounded-full border border-white/10" style={gradientStyle} />
          <span className="font-mono text-[9px] text-white/40">{displayUnit}</span>
        </div>
      )}
    </div>
  );
}
