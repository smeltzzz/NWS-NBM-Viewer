/**
 * Animation looping engine for the forecast timeline.
 *
 * A framework-agnostic transport controller:
 *   - rAF-driven frame accumulator (immune to timer throttling drift),
 *     adjustable 1–10 fps.
 *   - Continuous forward loop or "rocking" (ping-pong) loop.
 *   - Preloads the next 3 frames (travel-direction aware) through a
 *     {@link TilePreloader} while playing, so playback never blank-flashes.
 *   - Reports network buffering for the *current* frame so the timeline can
 *     show a spinner.
 *
 * Leak contract (verified by `scripts/verify-timeline-leaks.mjs`):
 *   - Exactly one rAF handle while playing, cancelled by `pause()`/`destroy()`.
 *   - One optional recheck `setTimeout` for the paused buffering indicator,
 *     always cleared and re-scheduled through a single handle.
 *   - `destroy()` disposes the preloader (all hidden images released) and
 *     detaches every subscriber. After `destroy()` the instance is inert.
 */

import { TilePreloader, type PreloaderStats } from './TilePreloader';
import { buildTimelineScale, clampIndex, type TimelineScale } from './frames';

/** Loop strategy: wrap around at the end, or ping-pong between the ends. */
export type LoopMode = 'forward' | 'rocking';

export const MIN_FPS = 1;
export const MAX_FPS = 10;
export const DEFAULT_FPS = 4;
/** Frames pre-fetched ahead of the playhead. */
export const PRELOAD_AHEAD = 3;
/** How long after a seek the buffering spinner may still report (ms). */
const SEEK_BUFFERING_WINDOW_MS = 1200;

export interface AnimationEngineSnapshot {
  playing: boolean;
  fps: number;
  loopMode: LoopMode;
  /** Current-frame tiles still downloading (spinner state). */
  buffering: boolean;
  index: number;
  hour: number;
  /** Total frames in the ladder. */
  count: number;
}

export type FrameChangeSource = 'engine' | 'external';

export interface AnimationEngineOptions {
  /** Forecast-hour ladder (sanitised internally). */
  hours: number[];
  initialIndex?: number;
  fps?: number;
  loopMode?: LoopMode;
  /** Frames to preload ahead of the playhead (default 3). */
  preloadAhead?: number;
  /**
   * Concrete tile URLs for a forecast hour (viewport-dependent). Returning an
   * empty list disables buffering detection for that frame.
   */
  getTileUrls?: (hour: number) => string[];
  /** Emitted whenever the playhead moves (`external` = echo of syncIndex). */
  onFrameChange?: (index: number, hour: number, source: FrameChangeSource) => void;
}

type Listener = (snapshot: AnimationEngineSnapshot) => void;

export class AnimationEngine {
  private scale: TimelineScale;
  private index: number;
  private fps: number;
  private loopMode: LoopMode;
  private readonly preloadAhead: number;
  private direction: 1 | -1 = 1;

  private playing = false;
  private buffering = false;
  private rafHandle: number | null = null;
  private lastTickTs: number | null = null;
  private accumulator = 0;

  private tileContext = '';
  private getTileUrls: ((hour: number) => string[]) | undefined;
  private onFrameChange: ((index: number, hour: number, source: FrameChangeSource) => void) | undefined;
  private preloader: TilePreloader;
  private recheckTimer: ReturnType<typeof setTimeout> | null = null;
  private recheckDeadline = 0;

  private listeners = new Set<Listener>();
  private destroyed = false;

  constructor(options: AnimationEngineOptions) {
    this.scale = buildTimelineScale(options.hours);
    this.fps = AnimationEngine.clampFps(options.fps ?? DEFAULT_FPS);
    this.loopMode = options.loopMode ?? 'forward';
    this.preloadAhead = Math.max(0, Math.floor(options.preloadAhead ?? PRELOAD_AHEAD));
    this.getTileUrls = options.getTileUrls;
    this.onFrameChange = options.onFrameChange;
    this.preloader = new TilePreloader({
      onSettle: () => this.refreshBuffering(),
    });
    this.index = clampIndex(options.initialIndex ?? 0, this.scale.count);
  }

  // ── Subscription ───────────────────────────────────────────────────────────

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.getSnapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  getSnapshot(): AnimationEngineSnapshot {
    return {
      playing: this.playing,
      fps: this.fps,
      loopMode: this.loopMode,
      buffering: this.buffering,
      index: this.index,
      hour: this.scale.hourAt(this.index),
      count: this.scale.count,
    };
  }

  private notify(): void {
    const snapshot = this.getSnapshot();
    for (const listener of [...this.listeners]) listener(snapshot);
  }

  // ── Transport ──────────────────────────────────────────────────────────────

  play(): void {
    if (this.destroyed || this.playing || this.scale.count < 2) return;
    this.playing = true;
    this.lastTickTs = null;
    this.accumulator = 0;
    // rAF loop: one cancellable handle for the whole session.
    this.rafHandle = requestAnimationFrame(this.tick);
    this.notify();
    this.preloadAheadOfPlayhead();
    this.refreshBuffering();
  }

  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    if (this.rafHandle !== null) {
      cancelAnimationFrame(this.rafHandle);
      this.rafHandle = null;
    }
    this.lastTickTs = null;
    this.accumulator = 0;
    this.notify();
    this.refreshBuffering();
  }

  togglePlay(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  get isPlaying(): boolean {
    return this.playing;
  }

  /** Preloader cache/in-flight stats — diagnostics and the leak audit. */
  get stats(): PreloaderStats {
    return this.preloader.stats();
  }

  /** Move ±`delta` frames (clamped at the ladder ends). */
  step(delta: number): void {
    this.seekIndex(this.index + Math.trunc(delta));
  }

  /** Jump ≈±24 h — to the frame nearest (current hour + delta). */
  jumpHours(deltaHours: number): void {
    const hour = this.scale.hourAt(this.index) + deltaHours;
    this.seekHour(hour);
  }

  first(): void {
    this.seekIndex(0);
  }

  last(): void {
    this.seekIndex(this.scale.count - 1);
  }

  /**
   * Reposition the playhead. Safe to call during playback (playback resumes
   * from the new position) and cheap when the position is unchanged.
   */
  seekIndex(index: number): void {
    if (this.destroyed) return;
    const next = clampIndex(index, this.scale.count);
    if (next === this.index) return;
    this.index = next;
    this.emitFrameChange('engine');
    this.noteSeek();
  }

  /** Seek to the frame nearest a forecast hour. */
  seekHour(hour: number): void {
    this.seekIndex(this.scale.indexOfHour(hour));
  }

  /**
   * External state → engine (React `forecastHour` prop changed outside the
   * timeline). Does not re-emit `onFrameChange`, so no feedback loop.
   */
  syncIndex(index: number): void {
    if (this.destroyed) return;
    const next = clampIndex(index, this.scale.count);
    if (next === this.index) return;
    this.index = next;
    this.emitFrameChange('external');
    this.noteSeek();
  }

  // ── Configuration ──────────────────────────────────────────────────────────

  setFps(fps: number): void {
    const next = AnimationEngine.clampFps(fps);
    if (next === this.fps || this.destroyed) return;
    this.fps = next;
    this.notify();
  }

  nudgeFps(delta: number): void {
    this.setFps(this.fps + Math.sign(delta));
  }

  static clampFps(fps: number): number {
    if (!Number.isFinite(fps)) return DEFAULT_FPS;
    return Math.min(MAX_FPS, Math.max(MIN_FPS, Math.round(fps)));
  }

  setLoopMode(mode: LoopMode): void {
    if (mode === this.loopMode || this.destroyed) return;
    this.loopMode = mode;
    this.direction = 1;
    this.notify();
  }

  toggleLoopMode(): void {
    this.setLoopMode(this.loopMode === 'forward' ? 'rocking' : 'forward');
  }

  /** Swap the ladder (e.g. run discovery posted different hours); clamps the playhead. */
  setHours(hours: number[]): void {
    if (this.destroyed) return;
    const next = buildTimelineScale(hours);
    if (next.count === this.scale.count && next.hours.every((h, i) => h === this.scale.hours[i])) {
      return;
    }
    this.scale = next;
    this.index = clampIndex(this.index, next.count);
    this.notify();
    this.preloader.retain(this.protectedKeys());
    this.refreshBuffering();
  }

  /**
   * Bind tile fetching to a new map context (domain|cycle|element). The epoch
   * is part of every cache key, and switching it releases the stale cache
   * immediately instead of waiting for LRU churn.
   */
  setTileContext(epoch: string, getTileUrls?: (hour: number) => string[]): void {
    if (this.destroyed) return;
    const urlsChanged = getTileUrls !== undefined && getTileUrls !== this.getTileUrls;
    if (epoch === this.tileContext && !urlsChanged) return;
    this.tileContext = epoch;
    if (getTileUrls !== undefined) this.getTileUrls = getTileUrls;
    this.preloader.clear();
    this.preloadAheadOfPlayhead();
    this.refreshBuffering();
  }

  // ── Preloading ─────────────────────────────────────────────────────────────

  /**
   * Preload the next `preloadAhead` frames starting at `index` (used for
   * scrub-bar hover previews and external nudges).
   */
  preloadFrom(index: number): void {
    if (this.destroyed) return;
    const start = clampIndex(index, this.scale.count);
    const forward = this.playing ? this.direction : 1;
    for (let step = 0; step < Math.max(1, this.preloadAhead); step++) {
      this.requestFrameTiles(this.neighbourIndex(start, forward * step));
    }
  }

  /** Preload the frames ahead of the current playhead (travel-direction aware). */
  private preloadAheadOfPlayhead(): void {
    const forward = this.playing ? this.direction : 1;
    for (let step = 0; step <= this.preloadAhead; step++) {
      this.requestFrameTiles(this.neighbourIndex(this.index, forward * step));
    }
  }

  private neighbourIndex(index: number, delta: number): number {
    const count = this.scale.count;
    if (count === 0) return 0;
    return ((index + delta) % count + count) % count;
  }

  private requestFrameTiles(index: number): void {
    if (!this.getTileUrls || this.scale.count === 0) return;
    const hour = this.scale.hours[index];
    if (hour === undefined) return;
    const urls = this.getTileUrls(hour);
    if (urls.length === 0) return;
    this.preloader.request(this.frameKey(index), urls);
  }

  private frameKey(index: number): string {
    const hour = this.scale.hourAt(index);
    return `${this.tileContext}#${hour}`;
  }

  private protectedKeys(): string[] {
    const keys = [this.frameKey(this.index)];
    const forward = this.playing ? this.direction : 1;
    for (let step = 1; step <= this.preloadAhead; step++) {
      keys.push(this.frameKey(this.neighbourIndex(this.index, forward * step)));
    }
    return keys;
  }

  // ── Buffering indicator ────────────────────────────────────────────────────

  private noteSeek(): void {
    this.preloadFrom(this.index);
    this.refreshBuffering();
  }

  private refreshBuffering(): void {
    const now = this.now();
    const withinSeekWindow = now < this.recheckDeadline;
    let buffering = false;
    if (this.getTileUrls && this.scale.count > 0 && (this.playing || withinSeekWindow)) {
      const urls = this.getTileUrls(this.scale.hourAt(this.index));
      if (urls.length > 0) buffering = !this.preloader.isReady(this.frameKey(this.index));
    }
    if (buffering !== this.buffering) {
      this.buffering = buffering;
      this.notify();
    }
    this.scheduleRecheck(buffering);
  }

  /**
   * While buffering while paused (recent seek), re-check until the frame
   * settles or the window expires — a single self-clearing timeout handle.
   */
  private scheduleRecheck(buffering: boolean): void {
    if (this.recheckTimer !== null) {
      clearTimeout(this.recheckTimer);
      this.recheckTimer = null;
    }
    if (!buffering) return;
    const remaining = this.recheckDeadline - this.now();
    if (remaining <= 0) return;
    this.recheckTimer = setTimeout(() => {
      this.recheckTimer = null;
      this.refreshBuffering();
    }, Math.min(remaining, 250));
  }

  // ── rAF playback loop ──────────────────────────────────────────────────────

  private readonly tick = (ts: number): void => {
    if (!this.playing || this.destroyed) return;
    if (this.lastTickTs !== null) {
      this.accumulator += Math.min(ts - this.lastTickTs, 3_000); // clamp tab-switch gaps
    }
    this.lastTickTs = ts;

    const interval = 1000 / this.fps;
    let advanced = 0;
    // At most 3 frames per tick — a slow frame budget drops time instead of
    // fast-forwarding the animation.
    while (this.accumulator >= interval && advanced < 3) {
      this.accumulator -= interval;
      this.advanceFrame();
      advanced += 1;
    }
    if (advanced === 3) this.accumulator = 0;

    if (advanced > 0) this.refreshBuffering();
    this.rafHandle = requestAnimationFrame(this.tick);
  };

  private advanceFrame(): void {
    const count = this.scale.count;
    if (count < 2) {
      this.pause();
      return;
    }
    let next: number;
    if (this.loopMode === 'rocking') {
      if (this.index + this.direction > count - 1 || this.index + this.direction < 0) {
        this.direction = (this.direction * -1) as 1 | -1;
      }
      next = this.index + this.direction;
    } else {
      next = this.index + this.direction;
      if (next > count - 1) next = 0;
      if (next < 0) next = count - 1;
    }
    this.index = next;
    this.emitFrameChange('engine');
    this.preloadAheadOfPlayhead();
  }

  private emitFrameChange(source: FrameChangeSource): void {
    this.onFrameChange?.(this.index, this.scale.hourAt(this.index), source);
    this.notify();
  }

  private now(): number {
    return typeof performance !== 'undefined' ? performance.now() : Date.now();
  }

  // ── Teardown ───────────────────────────────────────────────────────────────

  /**
   * Full teardown: cancels the rAF loop and recheck timer, releases every
   * cached/in-flight tile image and detaches listeners. The instance is inert
   * afterwards (all public methods no-op). Safe to call more than once.
   */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.playing = false;
    if (this.rafHandle !== null) {
      cancelAnimationFrame(this.rafHandle);
      this.rafHandle = null;
    }
    if (this.recheckTimer !== null) {
      clearTimeout(this.recheckTimer);
      this.recheckTimer = null;
    }
    this.listeners.clear();
    this.preloader.dispose();
  }
}
