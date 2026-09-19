'use client';

/**
 * React binding between {@link AnimationEngine} and the map shell.
 *
 * The engine is created once per mount and owns the animation clock; the
 * React tree stays the source of truth for the *current forecast hour*:
 *
 *   engine advance ──▶ onFrameChange ──▶ onHourChange (setState in MapShell)
 *   external change ──▶ engine.syncIndex (echo-free, no re-render loop)
 *
 * The hook returns a stable controller object for the dock components plus
 * the live snapshot (playing / fps / loopMode / buffering) for rendering.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import {
  AnimationEngine,
  DEFAULT_FPS,
  type AnimationEngineSnapshot,
  type LoopMode,
} from './AnimationEngine';
import { buildTimelineScale, sanitizeHours, type TimelineScale } from './frames';

export interface UseTimelineOptions {
  /** Forecast-hour ladder (from run discovery, canonical fallback). */
  hours: number[];
  /** Controlled current forecast hour (MapShell state). */
  hour: number;
  /** Called when the engine moves the playhead. */
  onHourChange: (hour: number) => void;
  /**
   * Tile-context epoch (`domain|cycle|element`). Changing it invalidates the
   * preloader cache — tiles are per-(cycle, element) after all.
   */
  tileEpoch?: string;
  /** Concrete viewport tile URLs per forecast hour (enables preloading). */
  getTileUrls?: (hour: number) => string[];
  initialFps?: number;
}

export interface TimelineController {
  /** Sanitised ladder and its scale helpers. */
  scale: TimelineScale;
  index: number;
  hour: number;
  playing: boolean;
  fps: number;
  loopMode: LoopMode;
  buffering: boolean;
  count: number;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  step: (delta: number) => void;
  jumpHours: (deltaHours: number) => void;
  seekIndex: (index: number) => void;
  seekHour: (hour: number) => void;
  first: () => void;
  last: () => void;
  setFps: (fps: number) => void;
  nudgeFps: (delta: number) => void;
  setLoopMode: (mode: LoopMode) => void;
  toggleLoopMode: () => void;
  /** Preload frames ahead of a scrub-bar hover position. */
  preloadFromIndex: (index: number) => void;
}

const EMPTY_SNAPSHOT: AnimationEngineSnapshot = {
  playing: false,
  fps: DEFAULT_FPS,
  loopMode: 'forward',
  buffering: false,
  index: 0,
  hour: 0,
  count: 0,
};

export function useTimeline(options: UseTimelineOptions): TimelineController {
  const { hour, onHourChange, tileEpoch, getTileUrls, initialFps } = options;
  const scale = useMemo(() => buildTimelineScale(options.hours), [options.hours]);

  // Latest callbacks without re-creating the engine.
  const callbacksRef = useRef({ onHourChange, getTileUrls });
  callbacksRef.current = { onHourChange, getTileUrls };

  const [engine, setEngine] = useState<AnimationEngine | null>(null);
  const [snapshot, setSnapshot] = useState<AnimationEngineSnapshot>(EMPTY_SNAPSHOT);

  // Create / destroy the engine exactly once per component lifetime.
  useEffect(() => {
    const instance = new AnimationEngine({
      hours: scale.hours,
      initialIndex: scale.indexOfHour(hour),
      fps: initialFps,
      getTileUrls: (h) => callbacksRef.current.getTileUrls?.(h) ?? [],
      onFrameChange: (_index, h) => callbacksRef.current.onHourChange(h),
    });
    const unsubscribe = instance.subscribe(setSnapshot);
    setEngine(instance);
    return () => {
      unsubscribe();
      instance.destroy();
      setEngine(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Ladder updates (run discovery refresh).
  useEffect(() => {
    engine?.setHours(scale.hours);
  }, [engine, scale]);

  // External playhead changes → sync into the engine without echoing back.
  useEffect(() => {
    engine?.syncIndex(scale.indexOfHour(hour));
  }, [engine, scale, hour]);

  // New cycle/domain/element → drop stale cached tiles.
  useEffect(() => {
    engine?.setTileContext(tileEpoch ?? '');
  }, [engine, tileEpoch]);

  // Stable action identities — the transport callbacks never change, so
  // consumers (e.g. the PlaybackControls hotkey effect) don't re-bind as the
  // snapshot updates every frame during playback.
  const actions = useMemo(
    () => ({
      play: () => engine?.play(),
      pause: () => engine?.pause(),
      togglePlay: () => engine?.togglePlay(),
      step: (delta: number) => engine?.step(delta),
      jumpHours: (deltaHours: number) => engine?.jumpHours(deltaHours),
      seekIndex: (index: number) => engine?.seekIndex(index),
      seekHour: (h: number) => engine?.seekHour(h),
      first: () => engine?.first(),
      last: () => engine?.last(),
      setFps: (fps: number) => engine?.setFps(fps),
      nudgeFps: (delta: number) => engine?.nudgeFps(delta),
      setLoopMode: (mode: LoopMode) => engine?.setLoopMode(mode),
      toggleLoopMode: () => engine?.toggleLoopMode(),
      preloadFromIndex: (index: number) => engine?.preloadFrom(index),
    }),
    [engine],
  );

  return useMemo(() => {
    const snap = engine !== null ? snapshot : EMPTY_SNAPSHOT;
    return {
      ...actions,
      scale,
      index: engine !== null ? snap.index : scale.indexOfHour(hour),
      hour: engine !== null ? snap.hour : hour,
      playing: snap.playing,
      fps: snap.fps,
      loopMode: snap.loopMode,
      buffering: snap.buffering,
      count: scale.count,
    };
  }, [actions, engine, snapshot, scale, hour]);
}

/** Re-export for convenience of consumers building tile URLs. */
export { sanitizeHours };
