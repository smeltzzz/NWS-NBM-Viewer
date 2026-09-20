/**
 * Pure canvas helpers for meteorological wind barbs and the wind-speed
 * colour ramp.
 *
 * Deliberately framework-free (no React, no globals other than the passed
 * 2D context) so the particle/barb pipeline can be exercised from Node
 * (`scripts/verify-visual-unmounts.mjs`) and unit-tested without a browser.
 */

export interface BarbOptions {
  /** Shaft length in px. Default 14. */
  size?: number;
  /** Stroke colour. Default translucent white (dark-map friendly). */
  color?: string;
  /** Stroke width. Default 1.4. */
  lineWidth?: number;
  /** Omit the flag triangle (speeds ≥ 50 kt). */
  noFlag?: boolean;
}

const DEG = Math.PI / 180;

/**
 * Draw a WMO/NWS-style wind barb centred on ``(x, y)``.
 *
 * The shaft points in the direction the wind is travelling (i.e. the arrow
 * head is downwind); feathers cluster on the upwind end, alternating
 * right/left, exactly as on operational charts:
 *
 *   flag (filled triangle) = 50 kt
 *   full feather           = 10 kt
 *   half feather           =  5 kt
 *
 * @param fromDirDeg  meteorological "from" direction in degrees true
 *                    (0 = wind from the north, 90 = from the east).
 * @param speedKt     wind speed in knots.
 */
export function drawWindBarb(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  fromDirDeg: number,
  speedKt: number,
  opts: BarbOptions = {},
): void {
  const L = opts.size ?? 14;
  const color = opts.color ?? 'rgba(255, 255, 255, 0.92)';
  const lineWidth = opts.lineWidth ?? 1.4;

  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  if (!Number.isFinite(fromDirDeg) || !Number.isFinite(speedKt) || speedKt < 0) return;

  // Barb decomposition (WMO/NWS convention).
  let rem = speedKt;
  let flags = 0;
  while (rem >= 50 && flags < 3 && !opts.noFlag) {
    flags += 1;
    rem -= 50;
  }
  const fulls = Math.floor(rem / 10);
  rem -= fulls * 10;
  const half = rem >= 2.5 ? 1 : 0;

  // No wind at all: draw a calm dot.
  if (flags === 0 && fulls === 0 && half === 0) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, Math.max(1.2, L * 0.09), 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return;
  }

  // Direction of travel = from + 180°.  Rotating the canvas by (θ+180) puts
  // local +y downwind, so the shaft runs tail (−L/2) → head (+L/2).
  const travel = (fromDirDeg % 360) + 180;

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(travel * DEG);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = 'round';

  // Shaft.
  ctx.beginPath();
  ctx.moveTo(0, -L / 2);
  ctx.lineTo(0, L / 2);
  ctx.stroke();

  const featherLen = L * 0.58;
  const halfLen = L * 0.36;
  const totalFeathers = fulls + half;

  // Flag(s) sit at the tail, then feathers follow downwind alternating
  // right / left of the shaft.
  let side = 1; // 1 = right, -1 = left (facing downwind)
  for (let f = 0; f < flags; f++) {
    const yTip = -L / 2;
    const yBase = -L / 2 + L * 0.46;
    ctx.beginPath();
    ctx.moveTo(0, yTip);
    ctx.lineTo(-L * 0.24, yBase);
    ctx.lineTo(L * 0.24, yBase);
    ctx.closePath();
    ctx.fill();
    side = -side;
  }
  for (let f = 0; f < totalFeathers; f++) {
    // Distribute feathers over the upwind 70 % of the shaft.
    const yAttach = -L / 2 + L * 0.72 * ((f + 0.5) / totalFeathers);
    const len = f === totalFeathers - 1 && half === 1 ? halfLen : featherLen;
    // 45° back toward the tail, alternating sides.
    const dx = side * Math.SQRT1_2 * len;
    const dy = -Math.SQRT1_2 * len;
    ctx.beginPath();
    ctx.moveTo(0, yAttach);
    ctx.lineTo(dx, yAttach + dy);
    ctx.stroke();
    side = -side;
  }

  ctx.restore();
}

// ── Wind speed → colour ramp ─────────────────────────────────────────────────
// Shared by the particle layer (per-segment stroke colour), the meteogram
// wind-pane barbs and the small HTML legend.  Knots.
interface SpeedStop {
  kt: number;
  rgb: [number, number, number];
}

const SPEED_STOPS: readonly SpeedStop[] = [
  { kt: 0, rgb: [148, 180, 214] }, // calm — slate
  { kt: 5, rgb: [64, 168, 255] }, // light
  { kt: 12, rgb: [32, 208, 224] }, // cyan
  { kt: 20, rgb: [72, 220, 110] }, // moderate
  { kt: 28, rgb: [232, 226, 60] }, // yellow
  { kt: 38, rgb: [255, 148, 32] }, // strong
  { kt: 50, rgb: [255, 52, 44] }, // gale
  { kt: 70, rgb: [255, 238, 255] }, // severe — near white
] as const;

/** Interpolated wind-speed colour (knots) as an ``rgba()`` string. */
export function windSpeedColor(speedKt: number, alpha = 1): string {
  if (!Number.isFinite(speedKt) || speedKt < 0) return `rgba(148,180,214,${alpha})`;
  const stops = SPEED_STOPS;
  const first = stops[0];
  const last = stops[stops.length - 1];
  if (!first || !last) return `rgba(148,180,214,${alpha})`;
  if (speedKt <= first.kt) return rgba(first.rgb, alpha);
  if (speedKt >= last.kt) return rgba(last.rgb, alpha);
  for (let i = 1; i < stops.length; i++) {
    const a = stops[i - 1];
    const b = stops[i];
    if (!a || !b) continue;
    if (speedKt <= b.kt) {
      const t = (speedKt - a.kt) / (b.kt - a.kt);
      const r = Math.round(a.rgb[0] + (b.rgb[0] - a.rgb[0]) * t);
      const g = Math.round(a.rgb[1] + (b.rgb[1] - a.rgb[1]) * t);
      const bl = Math.round(a.rgb[2] + (b.rgb[2] - a.rgb[2]) * t);
      return `rgba(${r},${g},${bl},${alpha})`;
    }
  }
  return rgba(last.rgb, alpha);
}

function rgba([r, g, b]: readonly [number, number, number], alpha: number): string {
  return `rgba(${r},${g},${b},${alpha})`;
}

/** CSS ``linear-gradient`` of the speed ramp for HTML legends. */
export function windSpeedGradient(): string {
  const parts = SPEED_STOPS.map((s) => {
    const pct = (s.kt / 70) * 100;
    return `rgb(${s.rgb[0]},${s.rgb[1]},${s.rgb[2]}) ${pct.toFixed(1)}%`;
  });
  return `linear-gradient(90deg, ${parts.join(', ')})`;
}
