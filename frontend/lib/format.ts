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

/** Colour ramp stops → CSS linear-gradient for the map legend. */
export function cssGradient(stops: { value: number; hex: string }[], angleDeg = 90): string {
  const parts = stops
    .slice()
    .sort((a, b) => a.value - b.value)
    .map((s) => `${s.hex} ${(s.value * 100).toFixed(0)}%`);
  return `linear-gradient(${angleDeg}deg, ${parts.join(', ')})`;
}
