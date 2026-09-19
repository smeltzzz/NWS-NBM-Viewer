'use client';

/**
 * Transport controls + keyboard hotkeys for the forecast timeline.
 *
 * Buttons: first (f001) · −24 h · step −1 · PLAY/PAUSE · step +1 · +24 h ·
 * last (f264), plus playback-speed (fps) and loop-mode (forward / rocking)
 * selectors.
 *
 * Global hotkeys (window-level, ignored while typing in form fields):
 *   ArrowLeft / ArrowRight — step −1 / +1
 *   Space                  — play / pause
 *   [ / ]                  — slower / faster (1–10 fps)
 *   Home / End             — first / last frame
 *
 * The listener is registered once on mount and always removed on unmount —
 * no stray window handlers after the dock goes away.
 */

import { useEffect } from 'react';

import { MAX_FPS, MIN_FPS, type LoopMode } from './AnimationEngine';

interface PlaybackControlsProps {
  /** Current frame index. */
  index: number;
  /** Total frames. */
  count: number;
  playing: boolean;
  fps: number;
  loopMode: LoopMode;
  onTogglePlay: () => void;
  /** Step ±delta frames. */
  onStep: (delta: number) => void;
  /** Jump ±hours to the nearest frame. */
  onJumpHours: (hours: number) => void;
  onFirst: () => void;
  onLast: () => void;
  onFpsChange: (fps: number) => void;
  onLoopModeChange: (mode: LoopMode) => void;
}

/** True when the hotkeys should be ignored (typing, modifiers, other widgets). */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}

const BTN_BASE =
  'inline-flex h-8 items-center justify-center rounded-md border text-white/75 transition select-none ' +
  'border-white/10 bg-white/5 hover:bg-white/10 hover:text-white active:scale-95 ' +
  'disabled:pointer-events-none disabled:opacity-30 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400';

export function PlaybackControls({
  index,
  count,
  playing,
  fps,
  loopMode,
  onTogglePlay,
  onStep,
  onJumpHours,
  onFirst,
  onLast,
  onFpsChange,
  onLoopModeChange,
}: PlaybackControlsProps) {
  const atStart = count === 0 || index <= 0;
  const atEnd = count === 0 || index >= count - 1;

  // ── Global hotkeys ────────────────────────────────────────────────────────
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;

      switch (event.key) {
        case 'ArrowLeft':
          event.preventDefault();
          onStep(-1);
          break;
        case 'ArrowRight':
          event.preventDefault();
          onStep(1);
          break;
        case ' ':
        case 'Spacebar':
          event.preventDefault();
          onTogglePlay();
          break;
        case '[':
          event.preventDefault();
          onFpsChange(fps - 1); // engine clamps to MIN_FPS
          break;
        case ']':
          event.preventDefault();
          onFpsChange(fps + 1); // engine clamps to MAX_FPS
          break;
        case 'Home':
          event.preventDefault();
          onFirst();
          break;
        case 'End':
          event.preventDefault();
          onLast();
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [fps, onFirst, onFpsChange, onJumpHours, onLast, onStep, onTogglePlay]);

  return (
    <div className="flex flex-none items-center gap-1">
      {/* First frame (f001) */}
      <button
        type="button"
        className={BTN_BASE}
        onClick={onFirst}
        disabled={atStart}
        title="First frame — f001 (Home)"
        aria-label="Jump to first forecast frame"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M6 5h2v14H6zM20 5v14L9 12z" />
        </svg>
      </button>

      {/* Jump −24 h */}
      <button
        type="button"
        className={`${BTN_BASE} gap-0.5 px-1.5 font-mono text-[10px] font-bold leading-none`}
        onClick={() => onJumpHours(-24)}
        title="Back 24 hours — same time yesterday"
        aria-label="Jump back 24 hours"
      >
        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M11 5v14L2 12zM22 5v14l-9-7z" />
        </svg>
        24h
      </button>

      {/* Step −1 */}
      <button
        type="button"
        className={BTN_BASE}
        onClick={() => onStep(-1)}
        disabled={atStart}
        title="Step back one frame (←)"
        aria-label="Step back one forecast frame"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M16 5v14L6 12z" />
        </svg>
      </button>

      {/* Play / pause */}
      <button
        type="button"
        className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600 text-white shadow-[0_2px_12px_rgba(56,189,248,0.45)] transition hover:brightness-110 active:scale-95 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-300"
        onClick={onTogglePlay}
        title={playing ? 'Pause (Space)' : 'Play loop (Space)'}
        aria-label={playing ? 'Pause forecast animation' : 'Play forecast animation'}
        aria-pressed={playing}
      >
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" />
          </svg>
        ) : (
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
            <path d="M8 5v14l11-7z" />
          </svg>
        )}
      </button>

      {/* Step +1 */}
      <button
        type="button"
        className={BTN_BASE}
        onClick={() => onStep(1)}
        disabled={atEnd}
        title="Step forward one frame (→)"
        aria-label="Step forward one forecast frame"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M8 5v14l10-7z" />
        </svg>
      </button>

      {/* Jump +24 h */}
      <button
        type="button"
        className={`${BTN_BASE} gap-0.5 px-1.5 font-mono text-[10px] font-bold leading-none`}
        onClick={() => onJumpHours(24)}
        title="Forward 24 hours — same time tomorrow"
        aria-label="Jump forward 24 hours"
      >
        24h
        <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M13 5v14l9-7zM2 5v14l9-7z" />
        </svg>
      </button>

      {/* Last frame (f264) */}
      <button
        type="button"
        className={BTN_BASE}
        onClick={onLast}
        disabled={atEnd}
        title="Last frame (End)"
        aria-label="Jump to last forecast frame"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M16 5h2v14h-2zM4 5v14l11-7z" />
        </svg>
      </button>

      <span className="mx-0.5 h-5 w-px bg-white/10" aria-hidden />

      {/* Playback speed — keyboard `[` / `]` or click */}
      <div
        className="flex h-8 items-center gap-1 rounded-md border border-white/10 bg-white/5 px-1"
        title="Playback speed — press [ or ] (1–10 fps)"
      >
        <button
          type="button"
          className="rounded px-1 text-[12px] font-bold leading-none text-white/60 transition hover:text-white"
          onClick={() => onFpsChange(Math.max(MIN_FPS, fps - 1))}
          aria-label="Decrease playback speed"
        >
          −
        </button>
        <span className="w-[34px] text-center font-mono text-[10px] font-bold leading-none text-white/80">
          {fps} fps
        </span>
        <button
          type="button"
          className="rounded px-1 text-[12px] font-bold leading-none text-white/60 transition hover:text-white"
          onClick={() => onFpsChange(Math.min(MAX_FPS, fps + 1))}
          aria-label="Increase playback speed"
        >
          +
        </button>
      </div>

      {/* Loop mode: continuous forward vs rocking */}
      <button
        type="button"
        className={`${BTN_BASE} gap-1 px-2 text-[10px] font-bold leading-none`}
        onClick={() => onLoopModeChange(loopMode === 'forward' ? 'rocking' : 'forward')}
        title={
          loopMode === 'forward'
            ? 'Loop mode: forward — click or press to switch to rocking'
            : 'Loop mode: rocking — click to switch to forward'
        }
        aria-label={`Loop mode: ${loopMode}. Click to toggle.`}
      >
        {loopMode === 'forward' ? (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M4 9a8 8 0 0 1 14-3M20 15a8 8 0 0 1-14 3" strokeLinecap="round" />
            <path d="M18 2v4h-4M6 22v-4h4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M7 4L3 8l4 4M3 8h13a4 4 0 0 1 4 4v1" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M17 20l4-4-4-4M21 16H8a4 4 0 0 1-4-4v-1" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {loopMode === 'forward' ? 'LOOP' : 'ROCK'}
      </button>
    </div>
  );
}
