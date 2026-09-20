'use client';

/**
 * Interactive station meteogram (AWIPS-style, 10-day / F001–F264).
 *
 * Opened when the user clicks a location on the map:
 *
 *   1. MapShell drops a draggable pinpoint marker at the click.
 *   2. This drawer fetches the complete time series from
 *      ``/api/v1/probe/meteogram?lat=…&lon=…&domain=…&cycle=…``.
 *   3. Four synchronized panes (Chart.js):
 *        • 2 m temperature + dewpoint with the daily min/max band
 *        • QPF bars, 10th–90th percentile envelope, snow/ice breakdown
 *        • sustained wind, gust envelope and meteorological wind barbs
 *        • sky cover (%) + ceiling height (AGL)
 *   4. Hovering any pane scrubs the *map timeline* to that forecast hour
 *      (crosshair is shared across all four panes).
 *   5. CSV / PNG export of the station time series.
 *
 * Unmount contract (verified by ``scripts/verify-visual-unmounts.mjs``):
 *   - the in-flight meteogram fetch is aborted,
 *   - every Chart.js instance is destroyed (``chart.destroy()``),
 *   - the shared-hover rAF is drained and no listeners survive.
 */

import { Chart, registerables, type ChartDataset, type Plugin } from 'chart.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { formatCycle, formatLatLon } from '@/lib/format';
import type { MeteogramResponse, NbmDomain } from '@/lib/types';

import {
  buildMeteogramCsv,
  buildMeteogramPanes,
  meteogramFileStem,
  type PaneSpec,
} from './meteogramData';
import { createSharedHover, makeCrosshairPlugin, makeWindBarbPlugin } from './chartPlugins';

// Chart.js is used imperatively here so the destroy() contract is explicit.
Chart.register(...registerables);

const MS_PER_KT = 1.9438444924;

interface MeteogramModalProps {
  lat: number;
  lon: number;
  domain: NbmDomain;
  cycle: string; // YYYYMMDDHH
  units: 'imperial' | 'metric';
  /** Nearest CWA city, resolved by MapShell (may be null). */
  cityName?: string | null;
  /** Chart hover/click → map timeline seeks to this forecast hour. */
  onScrub: (forecastHour: number) => void;
  onClose: () => void;
}

type FetchState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: MeteogramResponse };

const PANE_HEIGHTS_PX = [132, 118, 132, 118] as const;

export function MeteogramModal({
  lat,
  lon,
  domain,
  cycle,
  units,
  cityName,
  onScrub,
  onClose,
}: MeteogramModalProps) {
  const [fetchState, setFetchState] = useState<FetchState>({ status: 'loading' });
  const [retryNonce, setRetryNonce] = useState(0);
  const [entered, setEntered] = useState(false);

  const onScrubRef = useRef(onScrub);
  onScrubRef.current = onScrub;

  const canvasRefs = useRef<Array<HTMLCanvasElement | null>>([null, null, null, null]);
  const chartsRef = useRef<Array<Chart | null>>([null, null, null, null]);
  const panesRef = useRef<PaneSpec[] | null>(null);

  // ── Slide-in (transition from off-screen to rest) ────────────────────────
  useEffect(() => {
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, []);

  // ── Fetch the meteogram (aborted on close / station / cycle change) ─────
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setFetchState({ status: 'loading' });

    api
      .probeMeteogram({ lat, lon, domain, cycle, units, signal: controller.signal })
      .then((data) => {
        if (cancelled) return;
        setFetchState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        const status = (err as { status?: number })?.status;
        setFetchState({
          status: 'error',
          message:
            status === 422
              ? 'Location is outside the selected NBM domain.'
              : status === 404
                ? 'No forecast data available for this cycle.'
                : 'The meteogram service is unavailable. Try again in a moment.',
        });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [lat, lon, domain, cycle, units, retryNonce]);

  const data = fetchState.status === 'ready' ? fetchState.data : null;

  // ── Chart instances: create on data, destroy on every data change / close ─
  useEffect(() => {
    if (!data) return;

    const panes = buildMeteogramPanes(data);
    panesRef.current = panes;

    // One shared hover state drives the crosshair in all four panes and the
    // map-timeline scrub.  Cleared below on unmount.
    const firstPane = panes[0];
    const shared = createSharedHover((index) => {
      if (index === null || !firstPane) return;
      const hour = firstPane.hours[index];
      if (hour !== undefined) onScrubRef.current?.(hour);
    });

    const speedToKt =
      units === 'imperial'
        ? (s: number | null) => (s === null ? null : s)
        : (s: number | null) => (s === null ? null : s * MS_PER_KT);

    const created: Array<Chart | null> = [null, null, null, null];

    for (let i = 0; i < panes.length; i++) {
      const canvas = canvasRefs.current[i];
      if (!canvas || !canvas.isConnected) continue;
      const pane = panes[i];
      if (!pane) continue;

      const hiddenLabels = new Set(
        pane.datasets.filter((d) => d.hiddenFromLegend).map((d) => d.label),
      );

      const crosshair = makeCrosshairPlugin(shared);
      const plugins: Plugin[] = [crosshair];
      if (pane.id === 'wind') {
        plugins.push(
          makeWindBarbPlugin(
            data.series.map((p) => ({
              wind_speed: p?.wind_speed ?? null,
              wind_direction: p?.wind_direction ?? null,
            })),
            speedToKt,
          ),
        );
      }

      const chart = new Chart(canvas, {
        type: pane.chartType,
        data: {
          labels: pane.labels,
          datasets: pane.datasets.map((spec): ChartDataset => {
            if (spec.kind === 'bar') {
              return {
                type: 'bar',
                label: spec.label,
                data: spec.data,
                backgroundColor: spec.color,
                borderWidth: 0,
                order: spec.order ?? 0,
                stack: spec.stack,
                yAxisID: spec.axis ?? 'y',
                barPercentage: 0.92,
                categoryPercentage: 0.92,
              };
            }
            return {
              type: 'line',
              label: spec.label,
              data: spec.data,
              borderColor: spec.color,
              backgroundColor: spec.fill?.color ?? spec.color,
              borderWidth: spec.width ?? 1.5,
              borderDash: spec.dash,
              pointRadius: 0,
              pointHitRadius: 8,
              pointHoverRadius: 3,
              fill: spec.fill
                ? { target: spec.fill.target, above: spec.fill.color, below: spec.fill.color }
                : false,
              tension: 0.25,
              spanGaps: false,
              order: spec.order ?? 0,
              yAxisID: spec.axis ?? 'y',
            };
          }),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { mode: 'index', intersect: false },
          onHover: (_event, elements, chart) => {
            let index: number | null = null;
            const first = elements[0];
            if (first) {
              index = first.index;
            } else {
              // No dataset under the cursor — use the x-scale pixel.
              const native = (_event as { native?: { offsetX?: number } }).native;
              const x = native?.offsetX;
              const area = chart.chartArea;
              const xScale = chart.scales.x;
              if (typeof x === 'number' && area && xScale && x >= area.left && x <= area.right) {
                const value = xScale.getValueForPixel(x);
                if (typeof value === 'number' && Number.isFinite(value)) index = Math.round(value);
              }
            }
            if (index !== null && (index < 0 || index >= pane.hours.length)) index = null;
            shared.setIndex(index);
          },
          onClick: (_event, elements) => {
            const first = elements[0];
            if (first) {
              const hour = pane.hours[first.index];
              if (hour !== undefined) onScrubRef.current?.(hour);
            }
          },
          plugins: {
            legend: {
              display: true,
              position: 'top',
              align: 'end',
              labels: {
                color: 'rgba(255,255,255,0.6)',
                boxWidth: 9,
                boxHeight: 9,
                padding: 8,
                font: { size: 9 },
                filter: (item) => !hiddenLabels.has(item.text),
              },
            },
            tooltip: {
              backgroundColor: 'rgba(8, 13, 26, 0.94)',
              borderColor: 'rgba(255,255,255,0.14)',
              borderWidth: 1,
              titleColor: 'rgba(255,255,255,0.9)',
              bodyColor: 'rgba(255,255,255,0.72)',
              titleFont: { size: 10 },
              bodyFont: { size: 10 },
              padding: 8,
              boxWidth: 8,
              boxHeight: 8,
              filter: (item) => !hiddenLabels.has(item.dataset.label ?? ''),
              callbacks: {
                title: (items) => {
                  const i = items[0]?.dataIndex ?? 0;
                  const hour = pane.hours[i];
                  const vt = pane.validTimes[i];
                  if (hour === undefined) return '';
                  return `F${String(hour).padStart(3, '0')} — ${vt ?? ''}`;
                },
                label: (item) => {
                  const v = item.parsed?.y;
                  if (v === null || v === undefined) return undefined;
                  const decimals = pane.id === 'precipitation' ? 2 : 1;
                  return ` ${item.dataset.label}: ${Number(v).toFixed(decimals)}`;
                },
                afterBody: (items) => {
                  const i = items[0]?.dataIndex ?? 0;
                  const p = data.series[i];
                  if (!p) return [];
                  const extras: string[] = [];
                  if (p.precip_type_label && p.precip_type_label !== 'none') {
                    extras.push(`Precip type: ${p.precip_type_label}`);
                  }
                  if (typeof p.pop === 'number' && p.pop > 0) {
                    extras.push(`PoP (1 h): ${Math.round(p.pop)}%`);
                  }
                  return extras;
                },
              },
            },
          },
          scales: {
            x: {
              stacked: pane.stacked === true,
              grid: { color: 'rgba(255,255,255,0.05)' },
              ticks: {
                color: 'rgba(255,255,255,0.45)',
                maxRotation: 0,
                autoSkip: true,
                maxTicksLimit: 9,
                font: { size: 9 },
              },
            },
            y: {
              stacked: pane.stacked === true,
              beginAtZero: pane.id === 'precipitation',
              max: pane.yMax,
              grid: { color: 'rgba(255,255,255,0.07)' },
              title: {
                display: true,
                text: pane.yTitle,
                color: 'rgba(255,255,255,0.5)',
                font: { size: 9 },
              },
              ticks: {
                color: 'rgba(255,255,255,0.5)',
                font: { size: 9 },
                maxTicksLimit: 5,
              },
            },
            ...(pane.y1Title
              ? {
                  y1: {
                    position: 'right' as const,
                    grid: { display: false },
                    title: {
                      display: true,
                      text: pane.y1Title,
                      color: 'rgba(255,214,102,0.55)',
                      font: { size: 9 },
                    },
                    ticks: {
                      color: 'rgba(255,214,102,0.55)',
                      font: { size: 9 },
                      maxTicksLimit: 5,
                    },
                  },
                }
              : {}),
          },
        },
        plugins,
      });

      shared.charts.add(chart);
      created[i] = chart;
    }

    chartsRef.current = created;

    return () => {
      // Clean unmount: destroy every chart instance (Canvas contexts, event
      // listeners and the internal rAF are released by Chart.js) and drop the
      // shared hover state.
      for (const chart of chartsRef.current) {
        if (chart) chart.destroy();
      }
      chartsRef.current = [null, null, null, null];
      shared.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, units]);

  // ── Escape closes the drawer ──────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // ── Exports ───────────────────────────────────────────────────────────────
  const exportCsv = useCallback(() => {
    if (!data) return;
    const csv = buildMeteogramCsv(data);
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), `${meteogramFileStem(data.lat, data.lon, data.cycle)}.csv`);
  }, [data]);

  const exportPng = useCallback(() => {
    const charts = chartsRef.current.filter((c): c is Chart => c !== null);
    const panes = panesRef.current;
    if (charts.length === 0 || !panes || !data) return;

    const pad = 24;
    const headerH = 74;
    const gap = 18;
    const W = 1120;
    const innerW = W - pad * 2;

    const drawn: Array<{ chart: Chart; pane: PaneSpec; h: number }> = [];
    for (const chart of charts) {
      const src = chart.canvas;
      const h = Math.max(120, Math.round((src.height * innerW) / Math.max(1, src.width)));
      const pane = panes[drawn.length];
      if (pane) drawn.push({ chart, pane, h });
    }
    const H = headerH + drawn.reduce((acc, d) => acc + d.h, 0) + gap * (drawn.length + 1) + pad;

    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.fillStyle = '#0a1120';
    ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = '#f1f5f9';
    ctx.font = '600 20px Inter, system-ui, sans-serif';
    ctx.fillText('NWS NBM — 10-Day Station Meteogram', pad, 34);
    ctx.fillStyle = 'rgba(241,245,249,0.6)';
    ctx.font = '400 12px Inter, system-ui, sans-serif';
    ctx.fillText(
      `${formatLatLon(data.lat, data.lon, 3)}${cityName ? ` — ${cityName}` : ''}   ·   cycle ${formatCycle(data.cycle)}   ·   ${data.units} units   ·   F${String(data.start_fhour).padStart(3, '0')}–F${String(data.end_fhour).padStart(3, '0')} (${data.point_count} pts)   ·   generated ${new Date().toISOString().replace('T', ' ').slice(0, 16)}Z`,
      pad,
      56,
    );

    let y = headerH;
    for (const { chart, pane, h } of drawn) {
      ctx.fillStyle = 'rgba(241,245,249,0.78)';
      ctx.font = '600 13px Inter, system-ui, sans-serif';
      ctx.fillText(pane.title, pad, y + 4);
      ctx.drawImage(chart.canvas, pad, y + 12, innerW, h - 12);
      y += h + gap;
    }

    canvas.toBlob((blob) => {
      if (blob) downloadBlob(blob, `${meteogramFileStem(data.lat, data.lon, data.cycle)}.png`);
    }, 'image/png');
  }, [data, cityName]);

  const retry = useCallback(() => setRetryNonce((n) => n + 1), []);

  const body = useMemo(() => {
    if (fetchState.status === 'loading') {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-white/15 border-t-accent" />
          <p className="text-xs text-white/60">
            Fetching 10-day meteogram for {formatLatLon(lat, lon, 3)}…
          </p>
        </div>
      );
    }
    if (fetchState.status === 'error') {
      return (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <p className="text-sm font-medium text-amber-300/90">{fetchState.message}</p>
          <button
            type="button"
            onClick={retry}
            className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-semibold text-white/80 hover:border-accent/50 hover:text-white"
          >
            Retry
          </button>
        </div>
      );
    }
    return null;
  }, [fetchState, lat, lon, retry]);

  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-label={`Station meteogram at ${formatLatLon(lat, lon)}`}
      className={[
        'fixed z-50 flex flex-col overflow-hidden border-white/10 bg-[#0a1120]/[0.985] shadow-2xl backdrop-blur',
        // Mobile: bottom sheet. Desktop: right-hand drawer below the app
        // header (top-14 = 56 px header), so header controls stay reachable.
        'inset-x-0 bottom-0 top-[12dvh] rounded-t-2xl border-t',
        'sm:inset-x-auto sm:bottom-0 sm:right-0 sm:top-14 sm:w-[560px] sm:max-w-[94vw] sm:rounded-none sm:border-l sm:border-t-0',
        'transition-transform duration-200 ease-out',
        entered
          ? 'translate-x-0 translate-y-0'
          : 'translate-y-full sm:translate-x-full sm:translate-y-0',
      ].join(' ')}
    >
      {/* Header */}
      <header className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-bold tracking-wide text-white">
            10-Day Meteogram
            <span className="ml-2 text-[11px] font-medium text-accent">
              {cityName ?? formatLatLon(lat, lon, 3)}
            </span>
          </h2>
          <p className="mt-0.5 font-mono text-[10px] text-white/45">
            {formatLatLon(lat, lon, 3)} · cycle {formatCycle(cycle)} · {units} ·{' '}
            {data ? `F${String(data.start_fhour).padStart(3, '0')}–F${String(data.end_fhour).padStart(3, '0')}` : '—'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={exportCsv}
            disabled={!data}
            title="Export station time series as CSV"
            className="rounded-md border border-white/15 bg-white/5 px-2 py-1 text-[10px] font-semibold text-white/75 hover:border-accent/50 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            CSV
          </button>
          <button
            type="button"
            onClick={exportPng}
            disabled={!data}
            title="Export panes as a single PNG graphic"
            className="rounded-md border border-white/15 bg-white/5 px-2 py-1 text-[10px] font-semibold text-white/75 hover:border-accent/50 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            PNG
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close meteogram"
            className="ml-1 rounded-md border border-white/15 bg-white/5 px-2 py-1 text-[12px] font-semibold text-white/75 hover:border-red-400/60 hover:text-red-300"
          >
            ✕
          </button>
        </div>
      </header>

      {/* Panes */}
      <div className="flex-1 overflow-y-auto overscroll-contain">
        {body}
        {data && (
          <div className="flex flex-col gap-3 px-3 pb-3 pt-2">
            {[0, 1, 2, 3].map((i) => (
              <section
                key={i}
                aria-label={`Meteogram pane ${i + 1}`}
                className="rounded-lg border border-white/10 bg-white/[0.025] p-2"
              >
                <div
                  className="relative"
                  style={{ height: PANE_HEIGHTS_PX[i] }}
                >
                  <canvas
                    ref={(el) => {
                      canvasRefs.current[i] = el;
                    }}
                    aria-hidden
                  />
                </div>
              </section>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <footer className="border-t border-white/10 px-4 py-2">
        <p className="text-[10px] leading-4 text-white/40">
          Hourly to F036 · 3-hourly to F072 · 6-hourly to F264. Hover any pane to
          scrub the map timeline; click to pin the hour. Drag the map pin to
          re-sample the station.
        </p>
      </footer>
    </aside>
  );
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a beat to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 5_000);
}
