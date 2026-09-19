/**
 * Temporal scale utilities for the NBM forecast timeline.
 *
 * NBM publishes a non-uniform forecast ladder:
 *
 *   - hourly            f001 … f036
 *   - 3-hourly          f039 … f072
 *   - 6-hourly          f078 … f264
 *
 * The scrub bar uses *frame index space* as its linear coordinate (equal
 * spacing per frame) which makes the hour axis non-linear — early hours get
 * proportionally more room, exactly like Pivotal Weather / AWIPS time
 * controls. All mapping between index space and forecast hours lives here so
 * the slider, the animation engine and the preloader agree on one scale.
 *
 * This module is intentionally pure and DOM-free: it is unit-testable in
 * Node and safe to import from the leak-audit harness.
 */

// ── Canonical NBM forecast ladder ───────────────────────────────────────────

/** Publication cadence of the NBM core/QMD forecast layout. */
export const CANONICAL_HOUR_SEGMENTS: ReadonlyArray<{
  start: number;
  end: number;
  step: number;
}> = [
  { start: 1, end: 36, step: 1 }, // hourly f001–f036
  { start: 39, end: 72, step: 3 }, // 3-hourly f039–f072
  { start: 78, end: 264, step: 6 }, // 6-hourly f078–f264
];

/** The full canonical ladder (80 frames). Used when run discovery fails. */
export function canonicalForecastHours(): number[] {
  const hours: number[] = [];
  for (const seg of CANONICAL_HOUR_SEGMENTS) {
    for (let h = seg.start; h <= seg.end; h += seg.step) hours.push(h);
  }
  return hours;
}

/** Sort, dedupe and drop non-finite / negative hours. */
export function sanitizeHours(hours: readonly number[]): number[] {
  return [...new Set(hours)].filter((h) => Number.isFinite(h) && h >= 0).sort((a, b) => a - b);
}

// ── Index ⇄ hour scale ──────────────────────────────────────────────────────

export interface TimelineScale {
  /** Sanitised, ascending forecast hours (index space). */
  hours: number[];
  /** Number of frames. */
  count: number;
  /** First / last forecast hour (undefined only for an empty ladder). */
  firstHour: number | undefined;
  lastHour: number | undefined;
  /** Hour at an index (clamped). */
  hourAt: (index: number) => number;
  /** Nearest index for an hour (binary search). */
  indexOfHour: (hour: number) => number;
}

export function buildTimelineScale(hours: readonly number[]): TimelineScale {
  const list = sanitizeHours(hours);
  const hourAt = (index: number): number => {
    if (list.length === 0) return 0;
    return list[Math.min(Math.max(index, 0), list.length - 1)] ?? 0;
  };
  const indexOfHour = (hour: number): number => {
    if (list.length === 0) return 0;
    // Binary search for the nearest frame.
    let lo = 0;
    let hi = list.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if ((list[mid] ?? 0) < hour) lo = mid + 1;
      else hi = mid;
    }
    const atLo = list[lo] ?? 0;
    const beforeLo = list[lo - 1] ?? atLo;
    if (lo > 0 && Math.abs(beforeLo - hour) <= Math.abs(atLo - hour)) return lo - 1;
    return lo;
  };
  return {
    hours: list,
    count: list.length,
    firstHour: list[0],
    lastHour: list[list.length - 1],
    hourAt,
    indexOfHour,
  };
}

/** Clamp an index into [0, count-1] for a ladder of `count` frames. */
export function clampIndex(index: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(Math.max(Math.round(index), 0), count - 1);
}

// ── Cycle / valid-time helpers ──────────────────────────────────────────────

const CYCLE_RE = /^(\d{4})(\d{2})(\d{2})(\d{2})$/;
const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** Parse a `YYYYMMDDHH` cycle string into a UTC Date (null if malformed). */
export function cycleToDate(cycle: string): Date | null {
  const m = CYCLE_RE.exec(cycle);
  if (!m) return null;
  const date = new Date(
    Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4])),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

/** The UTC valid time of a forecast hour (cycle + fhour). */
export function validTimeFor(cycle: string, fhour: number): Date | null {
  const base = cycleToDate(cycle);
  if (!base) return null;
  return new Date(base.getTime() + fhour * 3_600_000);
}

/**
 * Prominent UTC valid-time banner, e.g. `Mon 18Z 23-OCT-2026`.
 */
export function formatValidBanner(valid: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${WEEKDAYS[valid.getUTCDay()]} ${pad(valid.getUTCHours())}Z ` +
    `${pad(valid.getUTCDate())}-${MONTHS[valid.getUTCMonth()]}-${valid.getUTCFullYear()}`
  );
}

/** Compact UTC hour label used on ticks/tooltip, e.g. `18Z`. */
export function formatZHour(valid: Date): string {
  return `${String(valid.getUTCHours()).padStart(2, '0')}Z`;
}

/** Compact tooltip stamp, e.g. `Thu 06Z 24-OCT`. */
export function formatValidShort(valid: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${WEEKDAYS[valid.getUTCDay()]} ${pad(valid.getUTCHours())}Z ` +
    `${pad(valid.getUTCDate())}-${MONTHS[valid.getUTCMonth()]}`
  );
}

/** Short date label for 00Z day ticks, e.g. `23-OCT`. */
export function formatDayLabel(valid: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(valid.getUTCDate())}-${MONTHS[valid.getUTCMonth()]}`;
}

/**
 * Local-wall-clock rendering of the valid time for the banner
 * (e.g. `Sun, Oct 19, 8:00 PM MDT`). Only called after mount so SSR and
 * client renders never disagree during hydration.
 */
export function formatLocalTime(valid: Date): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'shortGeneric',
    }).format(valid);
  } catch {
    return valid.toLocaleString();
  }
}

/** `F024` — forecast projection step label. */
export function forecastStepLabel(hour: number): string {
  return `F${String(Math.max(0, Math.round(hour))).padStart(3, '0')}`;
}

/** `+24h` — projection offset from model init. */
export function formatPlusHours(hour: number): string {
  return `+${Math.max(0, Math.round(hour))}h`;
}

// ── Track ticks ─────────────────────────────────────────────────────────────

export type TickKind = 'day' | 'synoptic' | 'step';

export interface TimelineTick {
  /** Frame index the tick is anchored to. */
  index: number;
  /** Position along the track, 0–1. */
  fraction: number;
  /** `day` = 00Z line, `synoptic` = 06/12/18Z, `step` = any other frame. */
  kind: TickKind;
  /** Label for 00Z day lines (`23-OCT`). */
  label?: string;
  /** Valid UTC hour at this frame (0–23). */
  utcHour: number;
  /** True when the frame lands on a precip-accumulation window boundary. */
  isAccumWindow: boolean;
}

/**
 * Compute the tick set for the scrub track.
 *
 * - **Day transitions**: frames whose *valid* UTC hour is 00 (the map re-draws
 *   a new calendar day) — tall tick + `DD-MMM` label.
 * - **Synoptic hours**: 06Z/12Z/18Z — medium tick.
 * - **Accumulation windows**: frames aligned to a valid 6-hour boundary,
 *   where the NBM QPF/POP accumulation buckets (qpf_6h/12h/24h, pop12) roll
 *   over — rendered as a distinct accent row by the UI.
 */
export function computeTicks(scale: TimelineScale, cycle: string): TimelineTick[] {
  const ticks: TimelineTick[] = [];
  for (let i = 0; i < scale.count; i++) {
    const hour = scale.hours[i];
    if (hour === undefined) break;
    const valid = validTimeFor(cycle, hour);
    if (!valid) break;
    const utcHour = valid.getUTCHours();
    const kind: TickKind = utcHour === 0 ? 'day' : utcHour % 6 === 0 ? 'synoptic' : 'step';
    ticks.push({
      index: i,
      fraction: scale.count > 1 ? i / (scale.count - 1) : 0,
      kind,
      label: kind === 'day' ? formatDayLabel(valid) : undefined,
      utcHour,
      isAccumWindow: utcHour % 6 === 0,
    });
  }
  return ticks;
}
