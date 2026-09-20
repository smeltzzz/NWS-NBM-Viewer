'use client';

/**
 * Panel-level error boundary with a friendly fallback + toast notification.
 *
 * The app shell is a dense set of independent panels (sidebar, legend,
 * timeline, meteogram drawer, map). A render-time throw in one panel — e.g. a
 * malformed product definition for an hour NOAA has not published — should
 * never blank the whole viewer: wrap each panel, show an inline card, and
 * surface a toast so the failure is *announced*, not silent.
 */

import { Component, type ErrorInfo, type ReactNode, useEffect, useRef } from 'react';

import { useToast } from './ToastProvider';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Human name of the wrapped region, used in the fallback + toast copy. */
  label: string;
  /**
   * Change `resetKey` to auto-clear a captured error (e.g. when the user
   * picks a different forecast hour / product).
   */
  resetKey?: string | number;
  className?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

class PanelErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the console breadcrumb for debugging production traces.
    // eslint-disable-next-line no-console
    console.error(`[NBM Viewer] ${this.props.label} crashed:`, error, info?.componentStack);
  }

  componentDidUpdate(prev: ErrorBoundaryProps): void {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <FallbackPanel
        label={this.props.label}
        detail={error.message || 'Unexpected client error'}
        onRetry={() => this.setState({ error: null })}
        className={this.props.className}
      />
    );
  }
}

/** Renders the visible fallback and pushes the one-shot toast. */
function FallbackPanel({
  label,
  detail,
  onRetry,
  className,
}: {
  label: string;
  detail: string;
  onRetry: () => void;
  className?: string;
}) {
  const toast = useToast();
  // Fire exactly once per capture — the toast system dedupes by key.
  useEffectOnce(() => {
    toast.push({
      key: `boundary:${label}`,
      tone: 'error',
      title: `${label} unavailable`,
      message: 'This panel hit an unexpected error and was isolated. The rest of the viewer still works.',
      ttlMs: 9000,
    });
  });

  return (
    <div
      role="alert"
      className={`flex flex-col items-center gap-2 rounded-xl border border-rose-400/30 bg-[#180d12]/90 p-4 text-center shadow-panel backdrop-blur ${className ?? ''}`}
    >
      <span className="text-lg" aria-hidden>
        ⚠️
      </span>
      <p className="text-sm font-semibold text-rose-100">{label} hit a problem</p>
      <p className="max-w-xs text-xs leading-snug text-rose-200/70">{detail}</p>
      <div className="mt-1 flex gap-2">
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-white/90 transition hover:bg-white/10 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-white/70 transition hover:bg-white/10 focus-visible:outline focus-visible:outline-1 focus-visible:outline-sky-400"
        >
          Reload page
        </button>
      </div>
    </div>
  );
}

// ── tiny local helper ───────────────────────────────────────────────────────
function useEffectOnce(fn: () => void): void {
  const ref = useRef(fn);
  ref.current = fn;
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    ref.current();
  }, []);
}

/** Public: wrap any panel. */
export { PanelErrorBoundary as ErrorBoundary };

/** App-level boundary: full-screen card + reload, used in layout.tsx. */
export function AppErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <PanelErrorBoundary
      label="NBM Viewer"
      resetKey={typeof window !== 'undefined' ? window.location.pathname : undefined}
    >
      {children}
    </PanelErrorBoundary>
  );
}
