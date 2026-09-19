'use client';

/**
 * Map legend: colour ramp for the active element. Ramps are shared with the
 * backend so the swatch always matches the rendered tiles.
 */

import { cssGradient } from '@/lib/format';
import { COLOUR_MAPS, colormapForElement } from '@/lib/nbm';

interface LegendProps {
  variable: string;
}

export function Legend({ variable }: LegendProps) {
  const key = colormapForElement(variable);
  const cmap = COLOUR_MAPS[key];
  if (!cmap) return null;

  return (
    <div className="pointer-events-auto rounded-lg border border-white/10 bg-surface-raised/95 p-3 shadow-panel backdrop-blur">
      <div className="mb-1.5 flex items-baseline justify-between gap-4">
        <span className="text-xs font-medium text-white/80">{cmap.label}</span>
        {cmap.units_hint ? (
          <span className="font-mono text-[10px] text-white/40">{cmap.units_hint}</span>
        ) : null}
      </div>
      <div
        className="h-2 w-48 rounded-full"
        style={{ background: cssGradient(cmap.stops, 90) }}
        role="img"
        aria-label={`${cmap.label} colour scale`}
      />
    </div>
  );
}
