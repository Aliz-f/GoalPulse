// Display helpers only. Amounts arrive as integer EUR cents and are never parsed back.

export function formatEurCents(cents: number, locale?: string): string {
  if (!Number.isInteger(cents)) throw new TypeError("amounts must be integer cents");
  return new Intl.NumberFormat(locale, { style: "currency", currency: "EUR" }).format(cents / 100);
}

/** 1440 -> "day" (bareSingular) / "1 day"; 360 -> "6 hours"; 45 -> "45 minutes". */
export function formatDuration(minutes: number, bareSingular = false): string {
  const units: [number, string][] = [
    [1440, "day"],
    [60, "hour"],
    [1, "minute"],
  ];
  for (const [size, unit] of units) {
    if (minutes % size === 0) {
      const n = minutes / size;
      if (n === 1) return bareSingular ? unit : `1 ${unit}`;
      return `${n} ${unit}s`;
    }
  }
  return `${minutes} minutes`;
}

/** Shows a UTC timestamp in the viewer's locale without altering the stored value. */
export function formatTimestamp(iso: string, locale?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(d);
}

/** Full UTC instant for hover text; preserves invalid input rather than inventing a time. */
export function timestampTitle(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString()} (UTC)`;
}

/** RFC 3339 permits offsets, so compare parsed instants rather than timestamp strings. */
export function newestTimestampFirst(a: string, b: string): number {
  const aMs = Date.parse(a);
  const bMs = Date.parse(b);
  if (Number.isNaN(aMs)) return Number.isNaN(bMs) ? 0 : 1;
  if (Number.isNaN(bMs)) return -1;
  return bMs - aMs;
}
