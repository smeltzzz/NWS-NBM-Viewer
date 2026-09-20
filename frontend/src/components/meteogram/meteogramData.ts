/**
 * Pure data shaping for the station meteogram: converts the
 * ``/probe/meteogram`` payload into the four AWIPS-style pane specs consumed
 * by the Chart.js layer, plus the CSV export.
 *
 * No React / Chart.js imports — the pane specs are plain data that the modal
 * maps onto Chart.js configs.  This keeps the numeric pipeline (nulls,
 * percentile envelopes, precip-type split, ceiling capping) testable in
 * Node via ``scripts/verify-visual-unmounts.mjs``.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Ceiling values above this (display units) are treated as "no ceiling". */
const CEILING_CAP_FT = 30_000;
const CEILING_CAP_M = 9_144;

/** Structural view of the API payload (mirrors {@link MeteogramResponse}). */
export interface MeteogramPointLike {
  forecast_hour: number;
  valid_time_utc: string;
  temperature: number | null;
  dewpoint: number | null;
  max_temperature: number | null;
  min_temperature: number | null;
  qpf: number | null;
  qpf_percentiles: { p10: number | null; p50: number | null; p90: number | null };
  snow: number | null;
  snow_percentiles: { p10: number | null; p50: number | null; p90: number | null };
  ice: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  wind_gust: number | null;
  sky_cover: number | null;
  ceiling_height: number | null;
  precip_type: number | null;
  precip_type_label: string | null;
  pop: number | null;
}

export interface MeteogramLike {
  lat: number;
  lon: number;
  domain: string;
  cycle: string;
  start_fhour: number;
  end_fhour: number;
  forecast_hours: number[];
  units: 'imperial' | 'metric';
  unit_labels: Record<string, string>;
  series: MeteogramPointLike[];
  point_count?: number;
  missing_count?: number;
}

export interface PaneDatasetSpec {
  label: string;
  data: (number | null)[];
  kind: 'line' | 'bar';
  color: string;
  dash?: number[];
  width?: number;
  /** Fill: relative dataset index (Chart.js `-1` = previous dataset) or axis. */
  fill?: { target: number | 'origin' | 'end'; color: string };
  stack?: string;
  axis?: 'y' | 'y1';
  /** Chart.js draw order (higher = further back). */
  order?: number;
  hiddenFromLegend?: boolean;
}

export type PaneId = 'temperature' | 'precipitation' | 'wind' | 'sky';

export interface PaneSpec {
  id: PaneId;
  title: string;
  chartType: 'line' | 'bar';
  labels: string[];
  hours: number[];
  validTimes: string[];
  datasets: PaneDatasetSpec[];
  yTitle: string;
  y1Title?: string;
  yMax?: number;
  stacked?: boolean;
}

/** "Sep 19 12Z" style compact valid-time label. */
export function shortValidTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${pad(d.getUTCHours())}Z`;
}

function num(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function labelOf(unitLabels: Record<string, string>, key: string, fallback = ''): string {
  const l = unitLabels[key];
  return typeof l === 'string' && l.length > 0 ? l : fallback;
}

/**
 * Build the four pane specs.  Dataset `data` arrays are parallel to
 * `labels`/`hours`/`validTimes`; missing values stay `null` (Chart.js breaks
 * the line rather than dragging it across gaps).
 */
export function buildMeteogramPanes(resp: MeteogramLike): PaneSpec[] {
  const s = resp.series;
  const n = s.length;
  const labels: string[] = new Array(n);
  const hours: number[] = new Array(n);
  const validTimes: string[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const p = s[i];
    if (!p) continue;
    labels[i] = shortValidTime(p.valid_time_utc);
    hours[i] = p.forecast_hour;
    validTimes[i] = p.valid_time_utc;
  }

  const u = (k: string) => labelOf(resp.unit_labels, k);

  // ── Pane 1: Temperature & dewpoint with daily min/max band ─────────────
  const tempUnit = u('temperature') || (resp.units === 'metric' ? '°C' : '°F');
  const temperature: PaneSpec = {
    id: 'temperature',
    title: `Temperature & Dewpoint (${tempUnit})`,
    chartType: 'line',
    labels,
    hours,
    validTimes,
    yTitle: tempUnit,
    datasets: [
      {
        label: `Max (${tempUnit})`,
        data: s.map((p) => num(p?.max_temperature)),
        kind: 'line',
        color: 'rgba(228, 87, 46, 0.55)',
        dash: [2, 3],
        width: 1,
        order: 1,
        hiddenFromLegend: false,
      },
      {
        label: `Min (${tempUnit})`,
        data: s.map((p) => num(p?.min_temperature)),
        kind: 'line',
        color: 'rgba(46, 158, 91, 0.55)',
        dash: [2, 3],
        width: 1,
        order: 1,
        fill: { target: -1, color: 'rgba(255, 255, 255, 0.06)' },
      },
      {
        label: `2m Temperature`,
        data: s.map((p) => num(p?.temperature)),
        kind: 'line',
        color: '#e4572e',
        width: 2,
        order: 0,
      },
      {
        label: `Dewpoint`,
        data: s.map((p) => num(p?.dewpoint)),
        kind: 'line',
        color: '#2e9e5b',
        width: 2,
        order: 0,
      },
    ],
  };

  // ── Pane 2: QPF bars + p10–p90 envelope + snow/ice split ────────────────
  const precipUnit = u('qpf') || (resp.units === 'metric' ? 'mm' : 'in');
  const precip: PaneSpec = {
    id: 'precipitation',
    title: `Precipitation — QPF & Probabilistic Envelope (${precipUnit})`,
    chartType: 'bar',
    labels,
    hours,
    validTimes,
    yTitle: precipUnit,
    stacked: true,
    datasets: [
      {
        label: `QPF p90`,
        data: s.map((p) => num(p?.qpf_percentiles?.p90)),
        kind: 'line',
        color: 'rgba(255, 196, 0, 0.35)',
        dash: [3, 3],
        width: 1,
        order: 2,
        // Own stack groups so the envelope lines are NOT stacked on top of
        // each other (or the bars) while the scale is stacked.
        stack: 'env-p90',
      },
      {
        label: `QPF p10–p90`,
        data: s.map((p) => num(p?.qpf_percentiles?.p10)),
        kind: 'line',
        color: 'rgba(255, 196, 0, 0)',
        width: 0,
        order: 2,
        stack: 'env-p10',
        fill: { target: -1, color: 'rgba(255, 196, 0, 0.16)' },
        hiddenFromLegend: true,
      },
      {
        label: `Rain (QPF)`,
        data: s.map((p) => {
          const p50 = p ? num(p.qpf_percentiles?.p50) : null;
          const total = num(p?.qpf) ?? p50;
          const snowAmt = num(p?.snow_percentiles?.p50) ?? num(p?.snow);
          const iceAmt = num(p?.ice);
          if (total === null && snowAmt === null && iceAmt === null) return null;
          return Math.max(0, (total ?? 0) - (snowAmt ?? 0) - (iceAmt ?? 0));
        }),
        kind: 'bar',
        color: 'rgba(47, 129, 247, 0.85)',
        stack: 'precip',
        order: 0,
      },
      {
        label: `Snow`,
        data: s.map((p) => num(p?.snow_percentiles?.p50) ?? num(p?.snow)),
        kind: 'bar',
        color: 'rgba(190, 224, 255, 0.9)',
        stack: 'precip',
        order: 0,
      },
      {
        label: `Ice`,
        data: s.map((p) => num(p?.ice)),
        kind: 'bar',
        color: 'rgba(179, 136, 255, 0.9)',
        stack: 'precip',
        order: 0,
      },
    ],
  };

  // ── Pane 3: Wind speed, gust envelope, barbs ────────────────────────────
  const windUnit = u('wind_speed') || (resp.units === 'metric' ? 'm/s' : 'kt');
  const wind: PaneSpec = {
    id: 'wind',
    title: `Wind — Sustained, Gust & Direction (${windUnit})`,
    chartType: 'line',
    labels,
    hours,
    validTimes,
    yTitle: windUnit,
    datasets: [
      {
        label: `Wind (sustained)`,
        data: s.map((p) => num(p?.wind_speed)),
        kind: 'line',
        color: '#ffa726',
        width: 2,
        order: 0,
      },
      {
        label: `Wind gust`,
        data: s.map((p) => num(p?.wind_gust)),
        kind: 'line',
        color: 'rgba(239, 83, 80, 0.9)',
        dash: [4, 3],
        width: 1.5,
        order: 1,
        fill: { target: -1, color: 'rgba(239, 83, 80, 0.12)' },
      },
    ],
  };

  // ── Pane 4: Sky cover + ceiling ─────────────────────────────────────────
  const ceilingUnit = u('ceiling_height') || (resp.units === 'metric' ? 'm' : 'ft');
  const ceilingCap = resp.units === 'metric' ? CEILING_CAP_M : CEILING_CAP_FT;
  const sky: PaneSpec = {
    id: 'sky',
    title: `Sky Cover & Ceiling`,
    chartType: 'line',
    labels,
    hours,
    validTimes,
    yTitle: '%',
    y1Title: `ceiling (${ceilingUnit} AGL)`,
    yMax: 100,
    datasets: [
      {
        label: `Sky cover (%)`,
        data: s.map((p) => {
          const v = num(p?.sky_cover);
          return v === null ? null : Math.min(100, Math.max(0, v));
        }),
        kind: 'line',
        color: 'rgba(64, 200, 230, 0.95)',
        width: 2,
        axis: 'y',
        order: 0,
        fill: { target: 'origin', color: 'rgba(64, 200, 230, 0.14)' },
      },
      {
        label: `Ceiling (${ceilingUnit} AGL)`,
        data: s.map((p) => {
          const v = num(p?.ceiling_height);
          // NBM marks clear sky with a sentinel height — cap it out.
          if (v === null || v <= 0 || v > ceilingCap) return null;
          return v;
        }),
        kind: 'line',
        color: 'rgba(255, 214, 102, 0.95)',
        dash: [5, 3],
        width: 1.5,
        axis: 'y1',
        order: 1,
      },
    ],
  };

  return [temperature, precip, wind, sky];
}

// ── CSV export ───────────────────────────────────────────────────────────────

const CSV_COLUMNS: Array<[string, (p: MeteogramPointLike) => number | string | null]> = [
  ['forecast_hour', (p) => p.forecast_hour],
  ['valid_time_utc', (p) => p.valid_time_utc],
  ['temperature', (p) => p.temperature],
  ['dewpoint', (p) => p.dewpoint],
  ['min_temperature', (p) => p.min_temperature],
  ['max_temperature', (p) => p.max_temperature],
  ['qpf', (p) => p.qpf],
  ['qpf_p10', (p) => p.qpf_percentiles?.p10 ?? null],
  ['qpf_p50', (p) => p.qpf_percentiles?.p50 ?? null],
  ['qpf_p90', (p) => p.qpf_percentiles?.p90 ?? null],
  ['snow', (p) => p.snow],
  ['snow_p10', (p) => p.snow_percentiles?.p10 ?? null],
  ['snow_p50', (p) => p.snow_percentiles?.p50 ?? null],
  ['snow_p90', (p) => p.snow_percentiles?.p90 ?? null],
  ['ice', (p) => p.ice],
  ['wind_speed', (p) => p.wind_speed],
  ['wind_direction', (p) => p.wind_direction],
  ['wind_gust', (p) => p.wind_gust],
  ['sky_cover_pct', (p) => p.sky_cover],
  ['ceiling_height', (p) => p.ceiling_height],
  ['precip_type', (p) => p.precip_type_label ?? p.precip_type ?? null],
  ['pop_pct', (p) => p.pop],
];

/** Full station time series as RFC-4180-ish CSV (header + one row per point). */
export function buildMeteogramCsv(resp: MeteogramLike): string {
  const u = (k: string) => labelOf(resp.unit_labels, k);
  const header = CSV_COLUMNS.map(([key, _get]) => {
    const unit = u(key);
    return unit ? `${key} (${unit})` : key;
  }).join(',');

  const lines: string[] = [header];
  for (const p of resp.series) {
    if (!p) continue;
    lines.push(
      CSV_COLUMNS.map(([_key, get]) => {
        const v = get(p);
        if (v === null || v === undefined) return '';
        const str = String(v);
        return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
      }).join(','),
    );
  }
  return lines.join('\n') + '\n';
}

/** Suggested file stem for exports: ``nbm-meteogram_39.74N_104.99W_2026091900``. */
export function meteogramFileStem(lat: number, lon: number, cycle: string): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `nbm-meteogram_${Math.abs(lat).toFixed(2)}${ns}_${Math.abs(lon).toFixed(2)}${ew}_${cycle}`;
}
