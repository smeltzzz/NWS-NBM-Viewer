'use client';

/**
 * Top toolbar: model/domain selection, product stream and forecast-hour scrubber.
 * Pure controlled component — the map shell owns the single source of truth.
 */

import type { NbmDomain, NbmProduct } from '@/lib/types';

import { formatCycle } from '@/lib/format';
import { DOMAINS, ELEMENTS, PRODUCTS } from '@/lib/nbm';

interface ToolbarProps {
  variable: string;
  domain: NbmDomain;
  product: NbmProduct;
  forecastHour: number;
  availableHours: number[];
  /** Active model cycle (YYYYMMDDHH) — displayed as a read-only chip. */
  cycle?: string;
  onVariableChange: (variable: string) => void;
  onDomainChange: (domain: NbmDomain) => void;
  onProductChange: (product: NbmProduct) => void;
  onForecastHourChange: (hour: number) => void;
}

export function Toolbar(props: ToolbarProps) {
  const {
    variable,
    domain,
    product,
    forecastHour,
    availableHours,
    cycle,
    onVariableChange,
    onDomainChange,
    onProductChange,
    onForecastHourChange,
  } = props;

  return (
    <div className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-lg border border-white/10 bg-surface-raised/95 px-3 py-2 shadow-panel backdrop-blur">
      {/* Variable */}
      <label className="flex items-center gap-2 text-xs text-white/60">
        Element
        <select
          value={variable}
          onChange={(e) => onVariableChange(e.target.value)}
          className="rounded-md border border-white/10 bg-surface-overlay px-2 py-1 text-sm text-white outline-none focus:border-accent"
        >
          {ELEMENTS.map((el) => (
            <option key={el.key} value={el.key}>
              {el.label} ({el.units})
            </option>
          ))}
        </select>
      </label>

      {/* Product stream */}
      <label className="flex items-center gap-2 text-xs text-white/60">
        Stream
        <select
          value={product}
          onChange={(e) => onProductChange(e.target.value as NbmProduct)}
          className="rounded-md border border-white/10 bg-surface-overlay px-2 py-1 text-sm text-white outline-none focus:border-accent"
        >
          {PRODUCTS.map((p) => (
            <option key={p.code} value={p.code}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {/* Domain */}
      <label className="flex items-center gap-2 text-xs text-white/60">
        Domain
        <select
          value={domain}
          onChange={(e) => onDomainChange(e.target.value as NbmDomain)}
          className="rounded-md border border-white/10 bg-surface-overlay px-2 py-1 text-sm text-white outline-none focus:border-accent"
        >
          {DOMAINS.map((d) => (
            <option key={d.code} value={d.code}>
              {d.label} — {d.resolution}
            </option>
          ))}
        </select>
      </label>

      {/* Active model cycle */}
      {cycle ? (
        <span
          className="rounded-md border border-white/10 bg-white/5 px-2 py-1 font-mono text-[11px] text-white/55"
          title="Active NBM model cycle (UTC)"
        >
          {formatCycle(cycle)}
        </span>
      ) : null}

      {/* Forecast hour */}
      <label className="flex flex-1 items-center gap-2 text-xs text-white/60">
        <span className="whitespace-nowrap">
          Hour <span className="font-mono text-accent">F{String(forecastHour).padStart(3, '0')}</span>
        </span>
        <input
          type="range"
          min={Math.min(...availableHours)}
          max={Math.max(...availableHours)}
          step={1}
          value={forecastHour}
          onChange={(e) => {
            let value = Number(e.target.value);
            // Snap to the nearest published hour.
            value = availableHours.reduce((prev, curr) =>
              Math.abs(curr - value) < Math.abs(prev - value) ? curr : prev,
            );
            onForecastHourChange(value);
          }}
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded bg-white/15 accent-accent"
        />
      </label>
    </div>
  );
}
