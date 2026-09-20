'use client';

/**
 * Pinpoint marker for the open station meteogram.
 *
 * A single MapLibre `Marker` (custom HTML element, draggable) is created on
 * first pick and repositioned when the station moves.  Dragging the pin
 * re-samples the station: `onDragEnd` fires with the new lat/lon and the
 * modal re-fetches its time series.
 *
 * Unmount contract: `marker.remove()` detaches the DOM element and all of
 * the marker's internal listeners (managed by MapLibre), and the ref is
 * cleared.
 */

import { useEffect, useRef } from 'react';

import mapboxgl from 'maplibre-gl';

import { useMap } from '@/components/map/MapContainer';

interface StationMarkerProps {
  station: { lat: number; lon: number } | null;
  onDragEnd: (lat: number, lon: number) => void;
}

function buildPinElement(): HTMLDivElement {
  const el = document.createElement('div');
  el.className = 'nbm-station-pin';
  el.setAttribute('role', 'img');
  el.setAttribute('aria-label', 'Station pin (drag to re-sample)');
  el.innerHTML =
    '<div class="nbm-station-pin__pulse"></div>' +
    '<div class="nbm-station-pin__ring"></div>' +
    '<div class="nbm-station-pin__dot"></div>';
  return el;
}

export function StationMarker({ station, onDragEnd }: StationMarkerProps) {
  const map = useMap();
  const markerRef = useRef<mapboxgl.Marker | null>(null);
  const onDragEndRef = useRef(onDragEnd);
  onDragEndRef.current = onDragEnd;

  // Create / move / remove the marker in response to station changes.
  useEffect(() => {
    if (!map) return;

    if (!station) {
      markerRef.current?.remove();
      markerRef.current = null;
      return;
    }

    if (!markerRef.current) {
      const marker = new mapboxgl.Marker({
        element: buildPinElement(),
        draggable: true,
        anchor: 'center',
      })
        .setLngLat([station.lon, station.lat])
        .addTo(map);

      marker.on('dragend', () => {
        const ll = marker.getLngLat();
        onDragEndRef.current?.(ll.lat, ll.lng);
      });
      markerRef.current = marker;
    } else {
      markerRef.current.setLngLat([station.lon, station.lat]);
    }
  }, [map, station]);

  // Remove the marker when the map instance goes away (or on unmount).
  useEffect(() => {
    return () => {
      markerRef.current?.remove();
      markerRef.current = null;
    };
  }, [map]);

  return null;
}
