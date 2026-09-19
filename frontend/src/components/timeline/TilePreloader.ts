/**
 * Intelligent tile preloader — warm the browser cache for upcoming forecast
 * frames using hidden `Image` elements.
 *
 * Why hidden `Image`s? MapLibre raster sources fetch tiles through the normal
 * HTTP cache. Fetching the *same URLs* beforehand with `new Image()` warms
 * that cache, so when the scrub bar lands on the frame MapLibre's request is
 * served from memory/disk without a round trip — no blank-frame flashes.
 *
 * Memory discipline (the leak-audit contract):
 *   - Images are never attached to the DOM (no render tree refs).
 *   - The loaded-frame cache is a bounded LRU (`maxCachedFrames`); eviction
 *     detaches handlers (`onload`/`onerror` → null) and clears `src` so the
 *     decoded bitmap is released instead of being pinned.
 *   - `dispose()` cancels every in-flight image and empties all registries;
 *     after it returns, this module holds no live objects or timers.
 */

export interface TilePreloaderOptions {
  /** Max frames kept in the LRU cache (default 24). */
  maxCachedFrames?: number;
  /** Max images fetching concurrently (default 24). */
  maxInFlight?: number;
  /** Called whenever an image settles (loads or fails) — used to refresh the buffering indicator. */
  onSettle?: () => void;
}

interface FrameEntry {
  key: string;
  /** url → image (complete or in-flight). */
  images: Map<string, HTMLImageElement>;
  /** URLs still downloading. */
  pending: Set<string>;
  /** LRU stamp (monotonic request counter). */
  lastAccess: number;
}

interface InFlightItem {
  key: string;
  url: string;
}

export interface PreloaderStats {
  cachedFrames: number;
  cachedImages: number;
  inFlight: number;
  queued: number;
}

export class TilePreloader {
  private frames = new Map<string, FrameEntry>();
  private queue: InFlightItem[] = [];
  /** image → the frame/url it is fetching (img.src is browser-resolved, so keep the origin strings). */
  private inFlight = new Map<HTMLImageElement, InFlightItem>();
  private readonly maxCachedFrames: number;
  private readonly maxInFlight: number;
  private readonly onSettle: (() => void) | undefined;
  private clock = 0;
  private disposed = false;

  constructor(options: TilePreloaderOptions = {}) {
    this.maxCachedFrames = Math.max(2, options.maxCachedFrames ?? 24);
    this.maxInFlight = Math.max(1, options.maxInFlight ?? 24);
    this.onSettle = options.onSettle;
  }

  /**
   * Request every tile of a frame. Dedupes by url; re-requesting bumps the
   * frame's LRU recency (so the current frame is never evicted mid-display).
   */
  request(key: string, urls: readonly string[]): void {
    if (this.disposed || urls.length === 0) return;
    const entry = this.frames.get(key) ?? this.createEntry(key);
    entry.lastAccess = ++this.clock;

    for (const url of urls) {
      if (entry.images.has(url)) continue;
      const image = new Image();
      // Hidden preload: never in the render tree, async decode, no layout.
      image.decoding = 'async';
      entry.images.set(url, image);
      entry.pending.add(url);
      this.queue.push({ key, url });
    }
    this.evictIfNeeded([key]);
    this.pump();
  }

  /** True when every requested url of the frame has settled (load or error). */
  isReady(key: string): boolean {
    const entry = this.frames.get(key);
    if (!entry) return false;
    return entry.pending.size === 0;
  }

  /** True when the frame has at least one cached image. */
  has(key: string): boolean {
    const entry = this.frames.get(key);
    return !!entry && entry.images.size > 0;
  }

  /** Drop everything except `protectedKeys` that exceeds the LRU bound. */
  retain(protectedKeys: readonly string[] = []): void {
    this.evictIfNeeded(protectedKeys);
  }

  stats(): PreloaderStats {
    let cachedImages = 0;
    for (const entry of this.frames.values()) cachedImages += entry.images.size;
    return {
      cachedFrames: this.frames.size,
      cachedImages,
      inFlight: this.inFlight.size,
      queued: this.queue.length,
    };
  }

  /** Release all cached frames and cancel in-flight work. */
  clear(): void {
    this.queue = [];
    for (const image of [...this.inFlight.keys()]) this.release(image);
    this.inFlight.clear();
    for (const entry of this.frames.values()) this.destroyEntry(entry);
    this.frames.clear();
  }

  /** Permanent teardown — the preloader must not be reused afterwards. */
  dispose(): void {
    this.clear();
    this.disposed = true;
  }

  // ── internals ─────────────────────────────────────────────────────────────

  private createEntry(key: string): FrameEntry {
    const entry: FrameEntry = { key, images: new Map(), pending: new Set(), lastAccess: 0 };
    this.frames.set(key, entry);
    return entry;
  }

  private pump(): void {
    if (this.disposed) return;
    while (this.queue.length > 0 && this.inFlight.size < this.maxInFlight) {
      const item = this.queue.shift();
      if (!item) break;
      const entry = this.frames.get(item.key);
      const image = entry?.images.get(item.url);
      if (!entry || !image) continue; // evicted while queued
      if (this.inFlight.has(image)) continue; // already fetching

      this.inFlight.set(image, item);
      image.onload = () => this.settle(image);
      image.onerror = () => this.settle(image);
      image.onabort = () => this.settle(image);
      // Errors are expected (missing frames) — readiness accounting treats
      // failures as settled so the spinner never wedges.
      image.src = item.url;
    }
  }

  private settle(image: HTMLImageElement): void {
    const meta = this.inFlight.get(image);
    if (!meta) return;
    this.inFlight.delete(image);
    image.onload = null;
    image.onerror = null;
    image.onabort = null;
    const entry = this.frames.get(meta.key);
    if (entry && entry.images.get(meta.url) === image) {
      entry.pending.delete(meta.url);
    }
    this.onSettle?.();
    this.pump();
  }

  private release(image: HTMLImageElement): void {
    image.onload = null;
    image.onerror = null;
    image.onabort = null;
    // Detach the decoded bitmap; the fetch itself cannot be aborted but the
    // response is discarded once no reference holds the element.
    image.src = '';
  }

  private destroyEntry(entry: FrameEntry): void {
    for (const image of entry.images.values()) this.release(image);
    entry.images.clear();
    entry.pending.clear();
  }

  private evictIfNeeded(protectedKeys: readonly string[]): void {
    if (this.frames.size <= this.maxCachedFrames) return;
    const protect = new Set(protectedKeys);
    // Oldest access first.
    const ordered = [...this.frames.values()].sort((a, b) => a.lastAccess - b.lastAccess);
    for (const entry of ordered) {
      if (this.frames.size <= this.maxCachedFrames) break;
      if (protect.has(entry.key)) continue;
      this.destroyEntry(entry);
      this.frames.delete(entry.key);
      this.queue = this.queue.filter((q) => q.key !== entry.key);
    }
  }
}
