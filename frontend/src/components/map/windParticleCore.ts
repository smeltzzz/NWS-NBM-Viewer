/**
 * Framework-free core of the animated wind overlay:
 *
 *   - {@link WindField}     — a U/V vector lattice (row-major, row 0 = north)
 *                             with bilinear sampling.
 *   - {@link WindParticleSystem} — the advected-particle pool: advance,
 *                             respawn and lifecycle (create / resize / destroy).
 *
 * Deliberately no React, no Canvas and no MapLibre: the React component
 * (`WindParticleLayer.tsx`) supplies a velocity sampler
 * (canvas px → lon/lat → bilinear U/V → px/s) and owns the drawing.  Keeping
 * the math here means the whole animation state machine can be driven from
 * Node (`scripts/verify-visual-unmounts.mjs`) and audited for leaks.
 */

export interface WindField {
  cols: number;
  rows: number;
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
  /** U (eastward) component, row-major, row 0 = north. NaN = missing. */
  u: Float32Array;
  /** V (northward) component, row-major, row 0 = north. NaN = missing. */
  v: Float32Array;
}

/** Build a {@link WindField} from the `/probe/wind-field` response arrays. */
export function windFieldFromResponse(resp: {
  bbox: [number, number, number, number];
  cols: number;
  rows: number;
  u: (number | null)[];
  v: (number | null)[];
}): WindField {
  const [minLon, minLat, maxLon, maxLat] = resp.bbox;
  const n = resp.cols * resp.rows;
  const u = new Float32Array(n);
  const v = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const uu = resp.u[i];
    const vv = resp.v[i];
    u[i] = typeof uu === 'number' && Number.isFinite(uu) ? uu : Number.NaN;
    v[i] = typeof vv === 'number' && Number.isFinite(vv) ? vv : Number.NaN;
  }
  return { cols: resp.cols, rows: resp.rows, minLon, minLat, maxLon, maxLat, u, v };
}

/**
 * Bilinear sample of a wind lattice at fractional (col, row) lattice indices
 * (row 0 = north edge).  Returns ``null`` when the point falls outside the
 * lattice or all four surrounding cells are missing.
 */
export function sampleWindField(
  field: WindField,
  colF: number,
  rowF: number,
): { u: number; v: number } | null {
  const { cols, rows, u, v } = field;
  if (!Number.isFinite(colF) || !Number.isFinite(rowF)) return null;
  if (colF < 0 || colF > cols - 1 || rowF < 0 || rowF > rows - 1) return null;

  const c0 = Math.floor(colF);
  const r0 = Math.floor(rowF);
  const c1 = Math.min(c0 + 1, cols - 1);
  const r1 = Math.min(r0 + 1, rows - 1);
  const dc = colF - c0;
  const dr = rowF - r0;

  const corners: Array<[number, number]> = [
    [r0 * cols + c0, (1 - dr) * (1 - dc)],
    [r0 * cols + c1, (1 - dr) * dc],
    [r1 * cols + c0, dr * (1 - dc)],
    [r1 * cols + c1, dr * dc],
  ];

  let accumU = 0;
  let accumV = 0;
  let weight = 0;
  for (const [idx, w] of corners) {
    const uu = u[idx];
    const vv = v[idx];
    if (
      typeof uu !== 'number' ||
      typeof vv !== 'number' ||
      !Number.isFinite(uu) ||
      !Number.isFinite(vv)
    ) {
      continue;
    }
    accumU += uu * w;
    accumV += vv * w;
    weight += w;
  }
  if (weight <= 1e-12) return null;
  return { u: accumU / weight, v: accumV / weight };
}

/** Meteorological "from" direction (degrees true) from U/V in m/s. */
export function windDirectionFromUV(uMs: number, vMs: number): number {
  // U = eastward, V = northward.  A wind FROM the north has V > 0, U = 0.
  const deg = (Math.atan2(-uMs, vMs) * 180) / Math.PI;
  return (deg + 360) % 360;
}

// ── Particle system ──────────────────────────────────────────────────────────

export interface Particle {
  x: number;
  y: number;
  prevX: number;
  prevY: number;
  age: number;
  maxAge: number;
}

export interface VelocitySample {
  /** Canvas px per second. */
  vx: number;
  vy: number;
  /** Local wind speed in knots (for colour mapping); NaN when missing. */
  speedKt: number;
}

export interface ParticleSystemOptions {
  width: number;
  height: number;
  /** Particle count. Default scales with the canvas area. */
  particleCount?: number;
  /** Deterministic RNG seed (audits). Default: entropy. */
  seed?: number;
  /** Max pixel displacement per frame (speed clamp). Default 4. */
  maxPxPerFrame?: number;
}

/** mulberry32 — tiny deterministic PRNG (audits need reproducible runs). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function defaultParticleCount(width: number, height: number): number {
  const area = Math.max(0, width) * Math.max(0, height);
  return Math.min(4000, Math.max(700, Math.round(area / 1300)));
}

export interface ParticleSystemStats {
  count: number;
  alive: number;
  respawns: number;
  destroyed: boolean;
  frames: number;
}

/**
 * Advected-particle pool.  The owning component calls {@link step} once per
 * animation frame with a sampler that converts canvas coordinates into
 * canvas-px/s velocities (the React layer folds in map projection, the U/V
 * field and the time-compression factor).
 *
 * Lifecycle: `new` → `step(...)`×n → `resize(...)`×n → `destroy()`.
 * After `destroy()` the pool is inert: `step()` is a no-op and the backing
 * storage is released (`particles.length === 0`).
 */
export class WindParticleSystem {
  readonly particles: Particle[] = [];
  private width: number;
  private height: number;
  private readonly maxPxPerFrame: number;
  private readonly rng: () => number;
  private respawns = 0;
  private frames = 0;
  private destroyed = false;

  constructor(opts: ParticleSystemOptions) {
    this.width = Math.max(1, opts.width);
    this.height = Math.max(1, opts.height);
    this.maxPxPerFrame = opts.maxPxPerFrame ?? 4;
    this.rng = mulberry32(opts.seed ?? (Date.now() & 0xffffffff));
    const count = opts.particleCount ?? defaultParticleCount(this.width, this.height);
    this.particles = new Array(count);
    for (let i = 0; i < count; i++) {
      this.particles[i] = this.makeParticle();
    }
  }

  private makeParticle(): Particle {
    const p: Particle = {
      x: this.rng() * this.width,
      y: this.rng() * this.height,
      prevX: 0,
      prevY: 0,
      age: this.rng() * 1.5, // stagger initial phases
      maxAge: 0.8 + this.rng() * 2.2,
    };
    p.prevX = p.x;
    p.prevY = p.y;
    return p;
  }

  /**
   * Advance every particle by ``dt`` seconds.  Returns per-particle velocity
   * samples (same order as {@link particles}) for the renderer to draw
   * segments and pick colours.  Calm/missing cells yield zero velocity — the
   * particle simply ages out and respawns.
   */
  step(sample: (x: number, y: number) => VelocitySample, dt: number): VelocitySample[] {
    if (this.destroyed) return [];
    if (!Number.isFinite(dt) || dt <= 0) return new Array(this.particles.length);
    const samples: VelocitySample[] = new Array(this.particles.length);
    const clampedDt = Math.min(0.1, dt);
    this.frames += 1;
    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      if (!p) continue;
      const s = sample(p.x, p.y);
      samples[i] = s;
      if (!s || !Number.isFinite(s.vx) || !Number.isFinite(s.vy)) {
        this.respawn(p);
        continue;
      }
      let dx = s.vx * clampedDt;
      let dy = s.vy * clampedDt;
      const dist = Math.hypot(dx, dy);
      if (dist > this.maxPxPerFrame) {
        const scale = this.maxPxPerFrame / dist;
        dx *= scale;
        dy *= scale;
      }
      p.prevX = p.x;
      p.prevY = p.y;
      p.x += dx;
      p.y += dy;
      p.age += clampedDt;
      const outOfBounds =
        p.x < -2 || p.x > this.width + 2 || p.y < -2 || p.y > this.height + 2;
      if (p.age >= p.maxAge || outOfBounds) {
        this.respawn(p);
      }
    }
    return samples;
  }

  private respawn(p: Particle): void {
    this.respawns += 1;
    const fresh = this.makeParticle();
    p.x = fresh.x;
    p.y = fresh.y;
    p.prevX = fresh.x;
    p.prevY = fresh.y;
    p.age = 0;
    p.maxAge = fresh.maxAge;
  }

  /** Re-size the pool (canvas/container resize).  Keeps the same particle set. */
  resize(width: number, height: number): void {
    if (this.destroyed) return;
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    // Recenter stragglers that now sit outside the new bounds.
    for (const p of this.particles) {
      if (p.x >= this.width || p.y >= this.height) {
        this.respawn(p);
      }
    }
  }

  stats(): ParticleSystemStats {
    return {
      count: this.particles.length,
      alive: this.destroyed ? 0 : this.particles.length,
      respawns: this.respawns,
      destroyed: this.destroyed,
      frames: this.frames,
    };
  }

  /**
   * Release the pool.  Idempotent; after this call the instance is inert
   * (``step`` returns an empty array, ``stats().destroyed`` is true and the
   * backing array is emptied so the renderer's references collect).
   */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.particles.length = 0;
  }
}
