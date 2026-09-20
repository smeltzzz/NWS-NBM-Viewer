'use client';

/**
 * Tiny dependency-free toast system for operational messaging.
 *
 * The viewer runs against a live upstream (NOAA publishes hourly and can be
 * late, partial, or rate-limited), so the UI needs a polite, non-blocking
 * channel for things like "Pop probability isn't available for F018 in this
 * cycle". Toasts auto-dismiss, deduplicate by key (tile errors fire per-tile;
 * we do NOT want 30 popups), and announce through an aria-live region.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

export type ToastTone = 'info' | 'success' | 'warning' | 'error';

export interface ToastInput {
  /** Human message (sentence case, actionable). */
  message: string;
  tone?: ToastTone;
  /** Optional short headline above the message. */
  title?: string;
  /** Auto-dismiss delay; 0 pins until dismissed. Default 7000 ms. */
  ttlMs?: number;
  /** Dedup key — pushing with an active key updates that toast instead of stacking. */
  key?: string;
}

interface ToastRecord extends Required<Omit<ToastInput, 'title' | 'key'>> {
  id: number;
  title?: string;
  key?: string;
}

interface ToastApi {
  push: (toast: ToastInput) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
const MAX_VISIBLE = 4;

const TONE_STYLES: Record<ToastTone, string> = {
  info: 'border-sky-400/40 bg-[#0c1a2e]/95 text-sky-100',
  success: 'border-emerald-400/40 bg-[#0b2419]/95 text-emerald-100',
  warning: 'border-amber-400/50 bg-[#241a08]/95 text-amber-100',
  error: 'border-rose-400/50 bg-[#260b10]/95 text-rose-100',
};

const TONE_ICON: Record<ToastTone, string> = {
  info: 'ℹ️',
  success: '✅',
  warning: '⚠️',
  error: '⛔',
};

/**
 * Ambient toast for components that only *report* (layer watchers, error
 * boundaries). Returns a stable no-op when no provider is mounted so shared
 * components never crash outside the app shell (tests, stories).
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  return (
    ctx ?? {
      push: () => 0,
      dismiss: () => undefined,
    }
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  // Synchronous mirror so push()/dismiss() can dedup + return ids without
  // racing React's lazy state updates (state setters run during render).
  const toastsRef = useRef<ToastRecord[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const commit = useCallback((next: ToastRecord[]) => {
    toastsRef.current = next;
    setToasts(next);
  }, []);

  const dismiss = useCallback(
    (id: number) => {
      const timer = timers.current.get(id);
      if (timer) {
        clearTimeout(timer);
        timers.current.delete(id);
      }
      commit(toastsRef.current.filter((t) => t.id !== id));
    },
    [commit],
  );

  const push = useCallback(
    (input: ToastInput) => {
      const ttlMs = input.ttlMs ?? 7000;
      // Dedup: refresh the visible copy instead of stacking duplicates.
      const existing = input.key ? toastsRef.current.find((t) => t.key === input.key) : undefined;
      const id = existing?.id ?? nextId.current++;
      const record: ToastRecord = {
        id,
        message: input.message,
        tone: input.tone ?? 'info',
        title: input.title,
        ttlMs,
        key: input.key,
      };
      const base = existing ? toastsRef.current.filter((t) => t.id !== id) : toastsRef.current;
      commit([...base, record].slice(-MAX_VISIBLE));

      // (Re)arm the auto-dismiss timer.
      const pending = timers.current.get(id);
      if (pending) clearTimeout(pending);
      if (ttlMs > 0) {
        timers.current.set(id, setTimeout(() => dismiss(id), ttlMs));
      }
      return id;
    },
    [commit, dismiss],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const t of map.values()) clearTimeout(t);
      map.clear();
    };
  }, []);

  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        aria-label="Status notifications"
        role="region"
        className="pointer-events-none fixed inset-x-0 top-16 z-[90] flex flex-col items-center gap-2 px-3 sm:inset-x-auto sm:right-4 sm:top-20 sm:items-end"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border px-3.5 py-2.5 shadow-[0_12px_36px_rgba(0,0,0,0.55)] backdrop-blur-xl ${TONE_STYLES[t.tone]}`}
          >
            <span aria-hidden className="mt-0.5 text-sm leading-none">
              {TONE_ICON[t.tone]}
            </span>
            <div className="min-w-0 flex-1">
              {t.title ? <p className="text-[12px] font-semibold leading-tight">{t.title}</p> : null}
              <p className="text-[12px] leading-snug opacity-90">{t.message}</p>
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="rounded-md px-1 text-base leading-none opacity-50 transition hover:opacity-100 focus-visible:outline focus-visible:outline-1 focus-visible:outline-white/60"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
