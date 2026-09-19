'use client';

/**
 * Map shell: full-viewport MapLibre map + overlay controls.
 * This is the top of the map-centric UI; everything below it is an overlay.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { MapView, type MapHandle, type NbmLayer } from '@/components/map/MapView';
import { Legend } from '@/components/map/Legend';
import { StatusBadge } from '@/components/map/StatusBadge';
import { Toolbar } from '@/components/map/Toolbar';
import { api } from '@/lib/api';
import { DOMAINS } from '@/lib/nbm';
import type { LayoutResponse, NbmDomain, NbmProduct } from '@/lib/types';

interface MapShellProps {
  initialVariable?: string;
  initialDomain?: NbmDomain;
  initialProduct?: NbmProduct;
  initialForecastHour?: number;
}

export function MapShell({
  initialVariable = 'tmp',
  initialDomain = 'co',
  initialProduct = 'core',
  initialForecastHour = 24,
}: MapShellProps) {
  const mapHandle = useRef<MapHandle | null>(null);

  const [variable, setVariable] = useState(initialVariable);
  const [domain, setDomain] = useState<NbmDomain>(initialDomain);
  const [product, setProduct] = useState<NbmProduct>(initialProduct);
  const [forecastHour, setForecastHour] = useState(initialForecastHour);
  const [availableHours, setAvailableHours] = useState<number[]>(defaultHours());

  const layer: NbmLayer = { variable, domain, product, forecastHour };

  // Keep the raster source in sync with selection state.
  useEffect(() => {
    mapHandle.current?.setNbmLayer(layer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variable, domain, product, forecastHour]);

  // Fetch the forecast-hour layout whenever domain/product changes.
  useEffect(() => {
    let cancelled = false;
    async function loadLayout() {
      try {
        const data = await api.getLayout(domain, product);
        if (cancelled) return;
        setAvailableHours(data.hours);
        setForecastHour((hour) => (data.hours.includes(hour) ? hour : data.hours[0] ?? hour));
      } catch {
        if (!cancelled) setAvailableHours(defaultHours());
      }
    }
    void loadLayout();
    return () => {
      cancelled = true;
    };
  }, [domain, product]);

  const handleDomainChange = useCallback(
    (next: NbmDomain) => {
      setDomain(next);
      const domainInfo = DOMAINS.find((d) => d.code === next);
      if (domainInfo) {
        // Re-center the camera on the new domain's approximate extent.
        const [minLon, minLat, maxLon, maxLat] = domainBbox(next);
        const center: [number, number] = [(minLon + maxLon) / 2, (minLat + maxLat) / 2];
        const zoom = next === 'oc' ? 2 : 5;
        mapHandle.current?.flyTo(center, zoom);
      }
    },
    [],
  );

  return (
    <div className="relative h-full w-full">
      <MapView ref={mapHandle} initialLayer={layer} className="absolute inset-0" />

      {/* Overlay chrome — pointer-events are scoped so map panning still works. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <Toolbar
            variable={variable}
            domain={domain}
            product={product}
            forecastHour={forecastHour}
            availableHours={availableHours}
            onVariableChange={setVariable}
            onDomainChange={handleDomainChange}
            onProductChange={setProduct}
            onForecastHourChange={setForecastHour}
          />
          <StatusBadge />
        </div>
      </div>

      <div className="pointer-events-none absolute bottom-6 right-4">
        <Legend variable={variable} />
      </div>
    </div>
  );
}

function defaultHours(): number[] {
  return [1, 6, 12, 18, 24, 36, 48, 72, 96, 120, 144, 168];
}

function domainBbox(domain: NbmDomain): [number, number, number, number] {
  const table: Record<NbmDomain, [number, number, number, number]> = {
    co: [-126, 20, -66, 50],
    ak: [-180, 50, -125, 72],
    hi: [-161, 18, -154, 23],
    pr: [-69, 17, -64, 19],
    gu: [140, 10, 150, 18],
    oc: [-180, -80, 180, 80],
  };
  return table[domain];
}
