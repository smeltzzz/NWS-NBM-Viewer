'use client';

/**
 * Temporal scrub bar — the fixed dock at the bottom of the map viewport.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────────────┐
 *   │ Valid: Mon 18Z 23-OCT-2026 · Local: Sun 12:00 PM MDT    ◌  F024 +24h│
 *   │ [transport controls]  ═══════════●══════════════════════════════    │
 *   │                        │ · · │ ▲20-OCT          ▬ accum windows    │
 *   └────────────────────────────────────────────────────────────────────┘
 *
 * - The slider maps slider position → frame **index**, which makes the
 *   non-uniform NBM ladder (hourly → 3-hourly → 6-hourly) evenly spaced:
 *   non-linear in hours, linear per frame.
 * - Click & drag scrub with pointer capture; every move seeks instantly
 *   (the raster layer double-buffers tile swaps, so this is flicker-free).
 * - Hovering previews the frame under the cursor (tooltip) and asks the
 *   parent to preload that frame + the 2 after it.
 * - Ticks: 00Z day transitions (tall + `DD-MMM` label), synoptic
 *   06/12/18Z, subtle per-frame minors, and a teal accumulation-window row
 *   marking the valid-time 6-hourly QPF/POP bucket boundaries.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  buildTimelineScale,
  computeTicks,
  cycleToDate,
  forecastStepLabel,
  formatLocalTime,
  formatPlusHours,
  formatValidBanner,
  formatValidShort,
  validTimeFor,
  type TimelineScale,
  type TimelineTick,
} from './frames';

interface TimelineBarProps {
  /** Forecast-hour ladder. */
  hours: number[];
  /** Model cycle `YYYYMMDDHH`. */
  cycle: string;
  /** Current forecast hour (controlled). */
  forecastHour: number;
  /** Current frame index (controlled by the timeline controller). */
  index: number;
  /** Seek to a frame index (called on every scrub/hover-activate). */
  onSeekIndex: (index: number) => void;
  /** Network buffering — shows the spinner. */
  buffering?: boolean;
  /** Transport controls rendered to the left of the track. */
  children?: React.ReactNode;
  /** Hover/scrub position changed — parent preloads upcoming frames. */
  onHoverIndex?: (index: number | null) => void;
}

export function TimelineBar({
  hours,
  cycle,
  forecastHour,
  index,
  onSeekIndex,
  buffering = false,
  children,
  onHoverIndex,
}: TimelineBarProps) {
  const scale: TimelineScale = useMemo(() => buildTimelineScale(hours), [hours]);
  const count = scale.count;

  // ── Banner times ──────────────────────────────────────────────────────────
  const validTime = useMemo(() => validTimeFor(cycle, forecastHour), [cycle, forecastHour]);
  // Local wall-clock is rendered post-mount only (SSR/client TZ mismatch).
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const initHour = useMemo(() => cycleToDate(cycle)?.getUTCHours() ?? null, [cycle]);

  // ── Ticks ─────────────────────────────────────────────────────────────────
  const ticks: TimelineTick[] = useMemo(() => computeTicks(scale, cycle), [scale, cycle]);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [trackWidth, setTrackWidth] = useState(0);
  useEffect(() => {
    const element = trackRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setTrackWidth(width);
    });
    observer.observe(element);
    setTrackWidth(element.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  // Minor per-frame ticks are dropped when they would be closer than 4 px.
  const visibleTicks = useMemo(() => {
    if (trackWidth <= 0) return ticks.filter((tick) => tick.kind !== 'step');
    const minGapPx = 4;
    let previousFraction = -Infinity;
    return ticks.filter((tick) => {
      if (tick.kind !== 'step') {
        previousFraction = tick.fraction;
        return true;
      }
      const px = tick.fraction * trackWidth;
      const keep = px - previousFraction * trackWidth >= minGapPx;
      if (keep) previousFraction = tick.fraction;
      return keep;
    });
  }, [ticks, trackWidth]);

  // ── Pointer scrubbing ─────────────────────────────────────────────────────
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<{ index: number; fraction: number } | null>(null);
  const lastHoverIndexRef = useRef<number | null>(null);

  const fraction = count > 1 ? index / (count - 1) : 0;
  const activeFraction = dragging ? fraction : hover !== null ? hover.fraction : fraction;

  const indexFromClientX = useCallback(
    (clientX: number): number | null => {
      const rect = trackRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || count < 2) return null;
      const pos = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return Math.round(pos * (count - 1));
    },
    [count],
  );

  const handlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      // Instantaneous scrub feedback: capture + seek on the initial press.
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragging(true);
      const next = indexFromClientX(event.clientX);
      if (next !== null) onSeekIndex(next);
    },
    [indexFromClientX, onSeekIndex],
  );

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const next = indexFromClientX(event.clientX);
      if (next === null) return;
      const nextFraction = next / (count - 1 || 1);
      if (event.currentTarget.hasPointerCapture(event.pointerId) || dragging) {
        onSeekIndex(next);
        setHover({ index: next, fraction: nextFraction });
        return;
      }
      if (hover === null || hover.index !== next) {
        setHover({ index: next, fraction: nextFraction });
      }
    },
    [count, dragging, hover, indexFromClientX, onSeekIndex],
  );

  const endDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  }, []);

  // Hover → preload upcoming frames (parent decides; deduped downstream).
  useEffect(() => {
    const activeIndex = dragging ? index : hover?.index ?? null;
    if (activeIndex === lastHoverIndexRef.current) return;
    lastHoverIndexRef.current = activeIndex;
    onHoverIndex?.(activeIndex);
  }, [dragging, hover, index, onHoverIndex]);

  // ── Tooltip text ──────────────────────────────────────────────────────────
  const tooltipHour = dragging ? forecastHour : hover !== null ? scale.hourAt(hover.index) : null;
  const tooltipText = useMemo(() => {
    if (tooltipHour === null) return null;
    const valid = validTimeFor(cycle, tooltipHour);
    if (!valid) return forecastStepLabel(tooltipHour);
    return `${forecastStepLabel(tooltipHour)} · ${formatValidShort(valid)}`;
  }, [cycle, tooltipHour]);

  return (
    <div className="nbm-timeline-safe-area pointer-events-none absolute inset-x-0 bottom-0 z-20 flex justify-center p-2 pb-3">
      <section
        aria-label="Forecast timeline"
        className="nbm-ui-no-select pointer-events-auto w-full max-w-[900px] rounded-2xl border border-white/10 bg-[#0c1424]/95 px-3 pb-1.5 pt-2 shadow-[0_12px_36px_rgba(0,0,0,0.6)] backdrop-blur-xl"
      >
        {/* Indeterminate buffering hairline */}
        <div className="relative -mx-3 -mt-2 mb-1.5 h-0.5 overflow-hidden rounded-t-2xl bg-transparent">
          {buffering && (
            <div className="absolute inset-y-0 w-1/3 animate-[timeline-buffer_1.1s_ease-in-out_infinite] rounded-full bg-gradient-to-r from-transparent via-sky-400 to-transparent" />
          )}
        </div>

        {/* Row A — valid time banner */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
          <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-white/40">
            Valid
          </span>
          <span className="font-mono text-[13px] font-bold leading-none text-sky-200">
            {validTime ? formatValidBanner(validTime) : '—'}
          </span>
          <span className="hidden text-[10px] font-medium leading-none text-white/45 sm:inline">
            {mounted && validTime ? `Local: ${formatLocalTime(validTime)}` : ''}
          </span>

          <span className="flex-1" />

          {buffering && (
            <svg
              role="status"
              aria-label="Buffering forecast tiles"
              className="h-3.5 w-3.5 animate-spin text-sky-300"
              viewBox="0 0 24 24"
              fill="none"
            >
              <title>Buffering forecast tiles…</title>
              <circle className="opacity-25" cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" />
              <path
                className="opacity-90"
                d="M21 12a9 9 0 0 0-9-9"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
              />
            </svg>
          )}

          {/* Forecast projection step */}
          <span className="flex items-baseline gap-1.5">
            <span className="rounded-md bg-sky-500/20 px-1.5 py-0.5 font-mono text-[12px] font-black leading-none text-sky-300 ring-1 ring-sky-400/30">
              {forecastStepLabel(forecastHour)}
            </span>
            <span className="font-mono text-[10px] font-semibold text-white/55">
              {formatPlusHours(forecastHour)}
            </span>
            {initHour !== null && (
              <span className="hidden text-[9px] font-medium uppercase tracking-wider text-white/35 md:inline">
                init {String(initHour).padStart(2, '0')}Z
              </span>
            )}
          </span>
        </div>

        {/* Row B — transport + scrub track */}
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 sm:flex-nowrap">
          {children}

          <div className="relative min-w-[140px] flex-1 pb-3.5 pt-1">
            <div
              ref={trackRef}
              role="slider"
              aria-label="Forecast hour scrub bar"
              aria-valuemin={0}
              aria-valuemax={Math.max(0, count - 1)}
              aria-valuenow={index}
              aria-valuetext={
                validTime
                  ? `${forecastStepLabel(forecastHour)}, ${formatValidBanner(validTime)}`
                  : forecastStepLabel(forecastHour)
              }
              tabIndex={0}
              className="relative h-6 cursor-pointer touch-none select-none outline-none"
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onPointerLeave={() => {
                if (!dragging) setHover(null);
              }}
            >
              {/* Rail */}
              <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-white/10" />
              {/* Fill */}
              <div
                className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-gradient-to-r from-sky-600 to-cyan-400"
                style={{ width: `${fraction * 100}%` }}
              />

              {/* Ticks */}
              {visibleTicks.map((tick) => (
                <div
                  key={tick.index}
                  className="pointer-events-none absolute top-0 h-full"
                  style={{ left: `${tick.fraction * 100}%` }}
                >
                  {tick.kind === 'day' && (
                    <>
                      <span className="absolute left-0 top-1/2 h-3.5 w-px -translate-x-1/2 -translate-y-1/2 bg-sky-300/70" />
                      <span className="absolute left-0 top-[19px] -translate-x-1/2 whitespace-nowrap font-mono text-[8px] font-semibold leading-none text-sky-200/70">
                        {tick.label}
                      </span>
                    </>
                  )}
                  {tick.kind === 'synoptic' && (
                    <span className="absolute left-0 top-1/2 h-2.5 w-px -translate-x-1/2 -translate-y-1/2 bg-white/25" />
                  )}
                  {tick.kind === 'step' && (
                    <span className="absolute left-0 top-1/2 h-1 w-px -translate-x-1/2 -translate-y-1/2 bg-white/15" />
                  )}
                </div>
              ))}

              {/* Accumulation-window markers (valid 6-hourly QPF/POP buckets) */}
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-1">
                {ticks
                  .filter((tick) => tick.isAccumWindow)
                  .map((tick) => (
                    <span
                      key={`accum-${tick.index}`}
                      title="Precipitation accumulation window boundary"
                      className="absolute top-0 h-1 w-[3px] -translate-x-1/2 rounded-sm bg-teal-400/60"
                      style={{ left: `${tick.fraction * 100}%` }}
                    />
                  ))}
              </div>

              {/* Hover ghost */}
              {!dragging && hover !== null && count > 1 && (
                <span
                  className="pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/60"
                  style={{ left: `${hover.fraction * 100}%` }}
                />
              )}

              {/* Thumb */}
              <span
                className={`pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-sky-300 bg-[#0c1424] shadow-[0_0_8px_rgba(56,189,248,0.7)] transition-transform ${
                  dragging ? 'scale-125' : 'scale-100'
                }`}
                style={{ left: `${fraction * 100}%` }}
              />

              {/* Scrub/hover tooltip */}
              {(dragging || hover !== null) && tooltipText && (
                <span
                  className="pointer-events-none absolute bottom-full mb-1 -translate-x-1/2 whitespace-nowrap rounded-md border border-white/10 bg-[#0f182b]/95 px-2 py-1 font-mono text-[10px] font-medium text-white/90 shadow-lg"
                  style={{
                    left: `${Math.min(96, Math.max(4, activeFraction * 100))}%`,
                  }}
                >
                  {tooltipText}
                </span>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
