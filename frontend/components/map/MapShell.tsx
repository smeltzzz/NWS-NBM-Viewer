'use client';

/**
 * Map shell: full-viewport MapLibre map + modern meteorological workstation UI.
 *
 * This is the top-level orchestrator for the new control suite:
 * - Header (top app bar): model cycle, domain pills, unit toggle, theme, fullscreen
 * - ProductSelector (left drawer / bottom sheet): hierarchical catalog, search, favorites
 * - Legend (floating colorbar): gradient, ticks, hovered value, opacity
 * - MapContainer + raster + vectors + probe
 *
 * Responsive: sidebar collapses to bottom sheet on mobile.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { computeFallbackCycle } from '@/lib/nbm';
import type { NbmDomain, NbmProduct, OverlayToggles } from '@/lib/types';
import type { UnitSystem } from '@/lib/units';

import { Header } from '@/components/layout/Header';
import { ProductSelector } from '@/components/controls/ProductSelector';
import { Legend as NewLegend } from '@/components/controls/Legend';
import { CATEGORIES, getProductById, type NBMProductDef, type ProductType, type Accumulation, type Percentile } from '@/components/controls/catalog';

import { PlaybackControls, TimelineBar, canonicalForecastHours, frameTileUrls, useTimeline } from '@timeline/index';

import { MapContainer, type MapHandle } from '@/components/map/MapContainer';
import { OverlayControls } from '@/components/map/OverlayControls';
import { ProbeReadout } from '@/components/map/ProbeReadout';
import { StatusBadge } from '@/components/map/StatusBadge';
import { VectorOverlays } from '@/components/map/VectorOverlays';
import { RASTER_DEFAULT_OPACITY, WeatherRasterLayer } from '@/components/map/WeatherRasterLayer';

const RUN_REFRESH_INTERVAL_MS = 20 * 60 * 1000;

interface MapShellProps {
  initialVariable?: string;
  initialDomain?: NbmDomain;
  initialProduct?: NbmProduct;
  initialForecastHour?: number;
}

const DEFAULT_TOGGLES: OverlayToggles = {
  states: true,
  counties: true,
  cwaa: true,
  highways: false,
  rivers: false,
  hillshade: true,
};

function useIsMobile(breakpoint = 1024) {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < breakpoint);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, [breakpoint]);
  return isMobile;
}

export function MapShell({
  initialVariable = 'tmp',
  initialDomain = 'co',
  initialProduct = 'core',
  initialForecastHour = 24,
}: MapShellProps) {
  // ── Core NBM selection ──────────────────────────────────────────────────
  const [variable, setVariable] = useState(initialVariable);
  const [domain, setDomain] = useState<NbmDomain>(initialDomain);
  const [product, setProduct] = useState<NbmProduct>(initialProduct);
  const [forecastHour, setForecastHour] = useState(initialForecastHour);
  const [cycle, setCycle] = useState<string>(() => computeFallbackCycle());
  const [availableHours, setAvailableHours] = useState<number[]>(() => canonicalForecastHours());
  const [availableCycles, setAvailableCycles] = useState<string[]>([]);
  const [runStatus, setRunStatus] = useState<'complete' | 'ingesting' | 'pending'>('complete');
  const [ingestionProgress, setIngestionProgress] = useState<number | undefined>(undefined);

  // ── UI state ────────────────────────────────────────────────────────────
  const [opacity, setOpacity] = useState<number>(RASTER_DEFAULT_OPACITY);
  const [toggles, setToggles] = useState<OverlayToggles>(DEFAULT_TOGGLES);
  const [cwaaAvailable, setCwaaAvailable] = useState(true);
  const [unitSystem, setUnitSystem] = useState<UnitSystem>('imperial');
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [hoveredValue, setHoveredValue] = useState<number | null>(null);

  // Product catalog state
  const [selectedProductId, setSelectedProductId] = useState<string>(() => {
    // Map initial variable to product id if possible
    const all = CATEGORIES.flatMap(c => c.products);
    const found = all.find(p => p.element === initialVariable || p.id === initialVariable);
    return found?.id ?? 'tmp';
  });
  const [selectedType, setSelectedType] = useState<ProductType>('deterministic');
  const [selectedPercentile, setSelectedPercentile] = useState<Percentile>(50);
  const [selectedAccum, setSelectedAccum] = useState<Accumulation>('24h');
  const [selectedThreshold, setSelectedThreshold] = useState<string | undefined>(undefined);

  const isMobile = useIsMobile(1024);

  // Live map instance — lets the preloader compute viewport tile coverage.
  const mapHandleRef = useRef<MapHandle | null>(null);

  // ── Temporal navigation controller ────────────────────────────────────────
  // Concrete per-frame tile URLs over the current viewport: the engine
  // preloads the next 3 frames through hidden Images while playing.
  const getTileUrls = useCallback(
    (hour: number) =>
      frameTileUrls(mapHandleRef.current?.getMap() ?? null, {
        domain,
        cycle,
        element: variable,
        fhour: hour,
      }),
    [domain, cycle, variable],
  );

  const timeline = useTimeline({
    hours: availableHours,
    hour: forecastHour,
    onHourChange: setForecastHour,
    tileEpoch: `${domain}|${cycle}|${variable}`,
    getTileUrls,
  });

  // On mobile, sidebar starts closed as bottom sheet
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
    else setSidebarOpen(true);
  }, [isMobile]);

  // Hydrate unit system and theme from localStorage
  useEffect(() => {
    try {
      const savedUnits = localStorage.getItem('nbm-unit-system') as UnitSystem | null;
      if (savedUnits === 'imperial' || savedUnits === 'metric') setUnitSystem(savedUnits);
      const savedTheme = localStorage.getItem('nbm-theme') as 'dark' | 'light' | null;
      if (savedTheme) {
        setTheme(savedTheme);
        document.documentElement.classList.remove('dark', 'light');
        document.documentElement.classList.add(savedTheme);
      }
    } catch {}
  }, []);

  useEffect(() => {
    try { localStorage.setItem('nbm-unit-system', unitSystem); } catch {}
  }, [unitSystem]);

  const handleThemeToggle = useCallback(() => {
    setTheme(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem('nbm-theme', next);
        document.documentElement.classList.remove('dark', 'light');
        document.documentElement.classList.add(next);
      } catch {}
      return next;
    });
  }, []);

  // ── Latest-run discovery ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    async function loadRun() {
      try {
        const run = await api.getLatestRun(domain, product);
        if (cancelled) return;
        if (run && run.date && run.cycle !== undefined) {
          const cycleStr = `${run.date}${String(run.cycle).padStart(2, '0')}`;
          setCycle(cycleStr);
          // Build recent cycles list (simulate last 12)
          const cycles: string[] = [];
          const base = new Date(Date.UTC(
            parseInt(run.date.slice(0,4),10),
            parseInt(run.date.slice(4,6),10)-1,
            parseInt(run.date.slice(6,8),10),
            run.cycle
          ));
          for (let i=0;i<16;i++) {
            const d = new Date(base);
            d.setUTCHours(d.getUTCHours()-i);
            const pad = (n:number)=>String(n).padStart(2,'0');
            cycles.push(`${d.getUTCFullYear()}${pad(d.getUTCMonth()+1)}${pad(d.getUTCDate())}${pad(d.getUTCHours())}`);
          }
          setAvailableCycles(cycles);

          const hours = run.available_forecast_hours ?? [];
          if (hours.length > 0) {
            setAvailableHours(hours);
            setForecastHour((h) => (hours.includes(h) ? h : hours[0] ?? h));
          } else {
            setAvailableHours(canonicalForecastHours());
          }

          // Run status: if latest cycle is recent, mark ingesting
          const ageMin = (Date.now() - base.getTime()) / 60000;
          if (ageMin < 45) {
            setRunStatus('ingesting');
            setIngestionProgress(Math.min(95, Math.max(20, Math.round(100 - ageMin))));
          } else {
            setRunStatus('complete');
            setIngestionProgress(undefined);
          }
        }
      } catch {
        if (!cancelled) {
          setCycle(computeFallbackCycle());
          setAvailableCycles([]);
          setRunStatus('complete');
        }
      }
    }

    void loadRun();
    const timer = setInterval(() => void loadRun(), RUN_REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [domain, product]);

  const handleToggle = useCallback((key: keyof OverlayToggles) => {
    setToggles((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const handleCwaaAvailable = useCallback((available: boolean) => {
    setCwaaAvailable(available);
  }, []);

  // Map selected product to element for raster
  const activeProductDef: NBMProductDef | undefined = useMemo(() => {
    return getProductById(selectedProductId);
  }, [selectedProductId]);

  // When product changes, sync variable and sub-selectors defaults
  const handleSelectProduct = useCallback((p: NBMProductDef) => {
    setSelectedProductId(p.id);
    setVariable(p.element);
    setSelectedType(p.defaultType);
    if (p.defaultPercentile) setSelectedPercentile(p.defaultPercentile);
    if (p.defaultAccum) setSelectedAccum(p.defaultAccum);
    setSelectedThreshold(undefined);
    // On mobile, close drawer after selection for map view
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);

  // Valid time display
  const validTime = useMemo(() => {
    if (!cycle || !/^\d{10}$/.test(cycle)) return undefined;
    try {
      const y = parseInt(cycle.slice(0,4),10);
      const m = parseInt(cycle.slice(4,6),10)-1;
      const d = parseInt(cycle.slice(6,8),10);
      const h = parseInt(cycle.slice(8,10),10);
      const base = new Date(Date.UTC(y,m,d,h));
      base.setUTCHours(base.getUTCHours()+forecastHour);
      const pad = (n:number)=>String(n).padStart(2,'0');
      return `${base.getUTCFullYear()}-${pad(base.getUTCMonth()+1)}-${pad(base.getUTCDate())} ${pad(base.getUTCHours())}:00Z`;
    } catch { return undefined; }
  }, [cycle, forecastHour]);

  return (
    <div className={`flex h-screen w-screen flex-col overflow-hidden ${theme === 'dark' ? 'bg-[#080e1c] text-slate-100' : 'bg-slate-50 text-slate-900'}`}>
      {/* Top Application Bar */}
      <Header
        domain={domain}
        onDomainChange={setDomain}
        cycle={cycle}
        onCycleChange={setCycle}
        availableCycles={availableCycles.length ? availableCycles : undefined}
        runStatus={runStatus}
        ingestionProgress={ingestionProgress}
        unitSystem={unitSystem}
        onUnitChange={setUnitSystem}
        theme={theme}
        onThemeToggle={handleThemeToggle}
        onToggleSidebar={() => setSidebarOpen(o => !o)}
        sidebarOpen={sidebarOpen}
      />

      {/* Main content: sidebar + map */}
      <div className="relative flex flex-1 overflow-hidden">
        {/* Product Selector - Desktop sidebar / Mobile bottom sheet */}
        <ProductSelector
          selectedProductId={selectedProductId}
          onSelectProduct={handleSelectProduct}
          selectedType={selectedType}
          onTypeChange={setSelectedType}
          selectedPercentile={selectedPercentile}
          onPercentileChange={setSelectedPercentile}
          selectedAccum={selectedAccum}
          onAccumChange={setSelectedAccum}
          selectedThreshold={selectedThreshold}
          onThresholdChange={setSelectedThreshold}
          unitSystem={unitSystem}
          isOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          isMobileSheet={isMobile}
          className={isMobile ? '' : 'top-0'}
        />

        {/* Map area */}
        <div className={`relative flex-1 ${!isMobile && sidebarOpen ? 'ml-[340px]' : 'ml-0'} transition-all duration-300`}>
          <MapContainer ref={mapHandleRef} domain={domain} className="absolute inset-0">
            <WeatherRasterLayer
              domain={domain}
              cycle={cycle}
              element={variable}
              fhour={forecastHour}
              opacity={opacity}
            />
            <VectorOverlays toggles={toggles} onCwaaAvailable={handleCwaaAvailable} />
            <ProbeReadout
              domain={domain}
              cycle={cycle}
              fhour={forecastHour}
              element={variable}
            />
          </MapContainer>

          {/* Temporal navigation dock — scrub bar + transport controls */}
          <TimelineBar
            hours={availableHours}
            cycle={cycle}
            forecastHour={forecastHour}
            index={timeline.index}
            onSeekIndex={timeline.seekIndex}
            buffering={timeline.buffering}
            onHoverIndex={(hoverIndex) => {
              if (hoverIndex !== null) timeline.preloadFromIndex(hoverIndex);
            }}
          >
            <PlaybackControls
              index={timeline.index}
              count={timeline.count}
              playing={timeline.playing}
              fps={timeline.fps}
              loopMode={timeline.loopMode}
              onTogglePlay={timeline.togglePlay}
              onStep={timeline.step}
              onJumpHours={timeline.jumpHours}
              onFirst={timeline.first}
              onLast={timeline.last}
              onFpsChange={timeline.setFps}
              onLoopModeChange={timeline.setLoopMode}
            />
          </TimelineBar>

          {/* Overlay controls - desktop left bottom (above the timeline dock) */}
          <div className="pointer-events-none absolute bottom-[112px] left-4 z-20 hidden lg:block">
            <OverlayControls
              opacity={opacity}
              onOpacityChange={setOpacity}
              toggles={toggles}
              onToggle={handleToggle}
              cwaaAvailable={cwaaAvailable}
            />
          </div>

          {/* Legend - floating (above the timeline dock) */}
          <div className="pointer-events-none absolute bottom-[112px] right-4 z-20 flex flex-col items-end gap-2">
            <NewLegend
              product={activeProductDef}
              variable={variable}
              unitSystem={unitSystem}
              opacity={opacity}
              onOpacityChange={setOpacity}
              forecastHour={forecastHour}
              cycle={cycle}
              validTime={validTime}
              hoveredValue={hoveredValue}
              onHoverValue={setHoveredValue}
              orientation={isMobile ? 'horizontal' : 'horizontal'}
            />
          </div>

          {/* Status badge - top right over map */}
          <div className="pointer-events-none absolute right-4 top-4 z-20 hidden md:block">
            <StatusBadge />
          </div>

          {/* Mobile overlay toggle button */}
          <div className="pointer-events-none absolute left-4 top-4 z-20 flex gap-2 lg:hidden">
            {!sidebarOpen && (
              <button
                type="button"
                onClick={() => setSidebarOpen(true)}
                className="pointer-events-auto inline-flex h-9 items-center gap-2 rounded-full border border-white/10 bg-[#0c1424]/90 px-3 text-[11px] font-semibold text-white/80 shadow-lg backdrop-blur"
              >
                <span className="text-[14px]">{activeProductDef ? CATEGORIES.find(c=>c.id===activeProductDef.category)?.icon : '🌡️'}</span>
                <span>{activeProductDef?.shortLabel ?? variable.toUpperCase()}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
