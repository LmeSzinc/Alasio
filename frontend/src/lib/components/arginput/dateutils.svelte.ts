import { DEFAULT_TIME, DEFAULT_TIME_DISPLAY } from "../arg/utils.svelte";

/**
 * The local timezone offset string, computed once on module load.
 * Format: `"+HH:mm"` or `"-HH:mm"` (e.g. `"+08:00"`, `"-05:00"`).
 */
export const tzOffset = (() => {
  const offset = -new Date().getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const absOffset = Math.abs(offset);
  const h = String(Math.floor(absOffset / 60)).padStart(2, "0");
  const m = String(absOffset % 60).padStart(2, "0");
  return `${sign}${h}:${m}`;
})();

/**
 * Convert an ISO datetime string to a local display string.
 *
 * Handles the sentinel `DEFAULT_TIME` by returning `DEFAULT_TIME_DISPLAY`.
 * Non-string / falsy / unparseable values are returned as-is.
 *
 * @param isoStr - ISO 8601 datetime string (e.g. `"2024-01-15T10:30:00Z"`)
 * @returns Local `"YYYY-MM-DD HH:mm:ss"` string in the user's timezone,
 *          or the original value if conversion fails.
 */
export function formatToLocal(isoStr: any): string {
  if (isoStr === DEFAULT_TIME) return DEFAULT_TIME_DISPLAY;
  if (typeof isoStr !== "string" || !isoStr) return String(isoStr || "");
  const date = new Date(isoStr);
  if (isNaN(date.getTime())) return isoStr;

  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    " " +
    pad(date.getHours()) +
    ":" +
    pad(date.getMinutes()) +
    ":" +
    pad(date.getSeconds())
  );
}

/**
 * Convert a local datetime string back to ISO UTC.
 *
 * Recognises the sentinel `DEFAULT_TIME_DISPLAY` and `DEFAULT_TIME` and
 * returns `DEFAULT_TIME`.  Emptry / unparseable values are returned as-is.
 * Milliseconds are stripped from the output.
 *
 * @param localStr - Local `"YYYY-MM-DD HH:mm:ss"` string
 * @returns ISO 8601 UTC string (e.g. `"2024-01-15T02:30:00.000Z"`),
 *          or the original value if conversion fails.
 */
export function parseToUTC(localStr: string): string {
  if (localStr === DEFAULT_TIME_DISPLAY || localStr === DEFAULT_TIME) return DEFAULT_TIME;
  if (!localStr) return "";
  const date = new Date(localStr);
  date.setMilliseconds(0);
  if (isNaN(date.getTime())) return localStr;
  const iso = date.toISOString();
  return iso;
}
