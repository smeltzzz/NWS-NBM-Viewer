/**
 * Chart.js plugins for the station meteogram.
 *
 *   - {@link createSharedHover}  — one hover index shared by all four panes;
 *     hovering any pane moves the crosshair in every pane (and drives the
 *     map-timeline scrub from the modal).
 *   - {@link makeCrosshairPlugin} — dashed vertical crosshair drawn at the
 *     shared index in each pane.
 *   - {@link makeWindBarbPlugin}  — meteorological barbs along the bottom of
 *     the wind pane, coloured by speed.
 *
 * Plugins are created per-modal (they close over the modal's shared state)
 * and destroyed with their charts — no global registration, no leaks.
 */

import type { Plugin } from 'chart.js';

import { drawWindBarb, windSpeedColor } from './barbs';

export interface SharedHover {
  /** Currently hovered forecast-hour index (or null). */
  index: number | null;
  /** Charts that redraw when the index changes. */
  charts: Set<object>;
  setIndex: (index: number | null) => void;
  clear: () => void;
}

/**
 * Create the cross-pane hover state.  `onIndexChange` fires synchronously on
 * every change (the modal uses it to scrub the map timeline); chart redraws
 * are coalesced into a single rAF.
 */
export function createSharedHover(onIndexChange?: (index: number | null) => void): SharedHover {
  const state: SharedHover = {
    index: null,
    charts: new Set<object>(),
    setIndex(index: number | null) {
      if (state.index === index) return;
      state.index = index;
      onIndexChange?.(index);
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => {
          for (const c of state.charts) {
            const draw = (c as { draw?: () => void }).draw;
            if (typeof draw === 'function') draw.call(c);
          }
        });
      }
    },
    clear() {
      state.index = null;
      state.charts.clear();
    },
  };
  return state;
}

/** Dashed vertical crosshair at the shared hover index. */
export function makeCrosshairPlugin(shared: SharedHover): Plugin<'line' | 'bar'> {
  return {
    id: 'meteogramCrosshair',
    afterDatasetsDraw(chart) {
      const idx = shared.index;
      if (idx === null || idx === undefined) return;
      const xScale = chart.scales.x;
      const area = chart.chartArea;
      if (!xScale || !area) return;
      const x = xScale.getPixelForValue(idx);
      if (!Number.isFinite(x) || x < area.left - 1 || x > area.right + 1) return;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = 'rgba(229, 231, 235, 0.55)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      ctx.restore();
    },
  };
}

export interface WindBarbPoint {
  wind_speed: number | null;
  wind_direction: number | null;
}

/**
 * Barbs row along the bottom of the wind pane.  `speedToKt` normalises the
 * display unit (kt or m/s) into knots for the colour ramp; pass the
 * identity for imperial data.
 */
export function makeWindBarbPlugin(
  series: WindBarbPoint[],
  speedToKt: (speed: number | null) => number | null,
): Plugin<'line'> {
  return {
    id: 'meteogramWindBarbs',
    afterDatasetsDraw(chart) {
      const area = chart.chartArea;
      const xScale = chart.scales.x;
      if (!area || !xScale || series.length === 0) return;
      const ctx = chart.ctx;
      // ~22 px per barb, capped so the row never overflows.
      const room = Math.floor((area.right - area.left) / 22);
      const step = Math.max(1, Math.ceil(series.length / Math.max(4, room)));
      ctx.save();
      for (let i = 0; i < series.length; i += step) {
        const p = series[i];
        if (!p || p.wind_speed === null || p.wind_direction === null) continue;
        const x = xScale.getPixelForValue(i);
        if (!Number.isFinite(x)) continue;
        const y = area.bottom - 11;
        const kt = speedToKt(p.wind_speed);
        drawWindBarb(
          ctx,
          x,
          y,
          p.wind_direction,
          kt ?? p.wind_speed,
          { size: 12, color: windSpeedColor(kt ?? 0, 0.9), lineWidth: 1.2 },
        );
      }
      ctx.restore();
    },
  };
}
