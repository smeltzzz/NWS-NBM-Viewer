'use client';

/**
 * Map shell: full-viewport MapLibre map + overlay controls.
 * This is the top of the map-centric UI; everything else floats above the map.
 *
 * State ownership: this component is the single source of truth for the
 * active NBM selection (element / domain / product / forecast hour / cycle),
 * the raster opacity and the overlay toggles. It discovers the latest
 * published cycle from the backend (`/runs/latest`) and falls back to a
 * client-computed cycle when the backend is unreachable.
 */

import { useCallback, useEffect, useState } from 'react';

import { api } from '@/lib/api';
import { computeFallbackCycle, defaultForecastHours } from '@/lib/nbm';
import type { NbmDomain, NbmProduct, OverlayToggles } from '@/lib/types';

import { Legend } from '@/components/map/Legend';
import { MapContainer } from '@/components/map/MapContainer';
import { OverlayControls } from '@/components/map/OverlayControls';
import { ProbeReadout } from '@/components/map/ProbeReadout';
import { StatusBadge } from '@/components/map/StatusBadge';
import { Toolbar } from '@/components/map/Toolbar';
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

export function MapShell({
  initialVariable = 'tmp',
  initialDomain = 'co',
  initialProduct = 'core',
  initialForecastHour = 24,
}: MapShellProps) {
  const [variable, setVariable] = useState(initialVariable);
  const [domain, setDomain] = useState<NbmDomain>(initialDomain);
  const [product, setProduct] = useState<NbmProduct>(initialProduct);
  const [forecastHour, setForecastHour] = useState(initialForecastHour);
  const [cycle, setCycle] = useState<string>(() => computeFallbackCycle());
  const [availableHours, setAvailableHours] = useState<number[]>(() => defaultForecastHours());
  const [opacity, setOpacity] = useState<number>(RASTER_DEFAULT_OPACITY);
  const [toggles, setToggles] = useState<OverlayToggles>(DEFAULT_TOGGLES);
  const [cwaaAvailable, setCwaaAvailable] = useState(true);

  // ── Latest-run discovery (cycle + posted forecast hours) ─────────────────
  useEffect(() => {
    let cancelled = false;

    async function loadRun() {
      try {
        const run = await api.getLatestRun(domain, product);
        if (cancelled) return;
        if (run && run.date && run.cycle !== undefined) {
          setCycle(`${run.date}${String(run.cycle).padStart(2, '0')}`);
          const hours = run.available_forecast_hours ?? [];
          if (hours.length > 0) {
            setAvailableHours(hours);
            setForecastHour((hour) => (hours.includes(hour) ? hour : hours[0] ?? hour));
          } else {
            setAvailableHours(defaultForecastHours());
          }
        }
      } catch {
        // Backend unreachable — keep the client-computed cycle as best effort.
        if (!cancelled) setCycle(computeFallbackCycle());
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

  return (
    <div className="relative h-full w-full">
      <MapContainer domain={domain} className="absolute inset-0">
        {/* Weather raster (below) and vector overlays (above) attach here. */}
        <WeatherRasterLayer
          domain={domain}
          cycle={cycle}
          element={variable}
          fhour={forecastHour}
          opacity={opacity}
        />
        <VectorOverlays toggles={toggles} onCwaaAvailable={handleCwaaAvailable} />
        <ProbeReadout domain={domain} cycle={cycle} fhour={forecastHour} element={variable} />
      </MapContainer>

      {/* Overlay chrome — pointer-events scoped so map panning still works. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex items-start justify-between gap-3 p-4 pr-12">
        <Toolbar
          variable={variable}
          domain={domain}
          product={product}
          forecastHour={forecastHour}
          availableHours={availableHours}
          cycle={cycle}
          onVariableChange={setVariable}
          onDomainChange={setDomain}
          onProductChange={setProduct}
          onForecastHourChange={setForecastHour}
        />
        <StatusBadge />
      </div>

      <div className="pointer-events-none absolute bottom-14 left-4 z-30">
        <OverlayControls
          opacity={opacity}
          onOpacityChange={setOpacity}
          toggles={toggles}
          onToggle={handleToggle}
          cwaaAvailable={cwaaAvailable}
        />
      </div>

      <div className="pointer-events-none absolute bottom-10 right-4 z-30">
        <Legend variable={variable} />
      </div>
    </div>
  );
}
