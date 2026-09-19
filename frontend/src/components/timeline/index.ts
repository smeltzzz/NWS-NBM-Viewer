/**
 * Temporal navigation suite for the NBM viewer.
 *
 * - `TimelineBar`        — fixed bottom dock: valid-time banner, non-linear
 *                          scrub track with 00Z/accumulation ticks.
 * - `PlaybackControls`   — transport buttons + global keyboard hotkeys.
 * - `AnimationEngine`    — rAF looping engine (1–10 fps, forward/rocking).
 * - `TilePreloader`      — hidden-Image frame preloader with bounded LRU.
 * - `useTimeline`        — React controller binding the engine to app state.
 * - `frames`             — forecast-hour ladder, scale math, time formatting.
 * - `viewportTiles`      — viewport → concrete tile URLs for preloading.
 */
export { AnimationEngine, MAX_FPS, MIN_FPS, DEFAULT_FPS, PRELOAD_AHEAD } from './AnimationEngine';
export type { AnimationEngineSnapshot, LoopMode } from './AnimationEngine';
export { TilePreloader } from './TilePreloader';
export { TimelineBar } from './TimelineBar';
export { PlaybackControls } from './PlaybackControls';
export { useTimeline } from './useTimeline';
export type { TimelineController, UseTimelineOptions } from './useTimeline';
export { frameTileUrls } from './viewportTiles';
export {
  CANONICAL_HOUR_SEGMENTS,
  canonicalForecastHours,
  sanitizeHours,
  buildTimelineScale,
  clampIndex,
  cycleToDate,
  validTimeFor,
  formatValidBanner,
  formatValidShort,
  formatZHour,
  formatDayLabel,
  formatLocalTime,
  forecastStepLabel,
  formatPlusHours,
  computeTicks,
} from './frames';
export type { TimelineScale, TimelineTick, TickKind } from './frames';
