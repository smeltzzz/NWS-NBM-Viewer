/** Formatting helpers shared across the map UI. */

/** Forecast hour → "F024 (+24h)" style label. */
export function forecastHourLabel(hour: number): string {
  return `F${String(hour).padStart(3, '0')}`;
}

/** Seconds-since-epoch → "YYYY-MM-DD HH:MMZ". */
export function formatUtc(isoOrEpoch: string | number): string {
  const date =
    typeof isoOrEpoch === 'number' ? new Date(isoOrEpoch * 1000) : new Date(isoOrEpoch);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}Z`
  );
}

/** Cycle "YYYYMMDDHH" → "2026-09-19 12Z" compact chip label. */
export function formatCycle(cycle: string): string {
  if (!/^\d{8,10}$/.test(cycle)) return cycle;
  const d = cycle.slice(0, 8);
  const h = cycle.slice(8, 10).padEnd(2, '0');
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${h}Z`;
}

/** WGS84 lat/lon → "39.74° N, 104.99° W" style readout. */
export function formatLatLon(lat: number, lon: number, digits = 2): string {
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}

/** One probe value → compact "74.2 °F" / "18 kt" / "Light Snow" string. */
export function formatProbeValue(value: {
  value: number | null;
  units: string;
  formatted?: string;
  category?: string;
  missing?: boolean;
}): string | null {
  if (value.missing || (value.value === null && !value.category)) return null;
  if (value.formatted) return value.formatted;
  if (value.category) return value.category;
  if (value.value === null) return null;
  const rounded = Math.abs(value.value) >= 100 ? value.value.toFixed(0) : value.value.toFixed(1);
  return `${rounded} ${value.units}`.trim();
}

/** Colour ramp stops → CSS linear-gradient for the map legend. */
export function cssGradient(stops: { value: number; hex: string }[], angleDeg = 90): string {
  const parts = stops
    .slice()
    .sort((a, b) => a.value - b.value)
    .map((s) => `${s.hex} ${(s.value * 100).toFixed(0)}%`);
  return `linear-gradient(${angleDeg}deg, ${parts.join(', ')})`;
}
