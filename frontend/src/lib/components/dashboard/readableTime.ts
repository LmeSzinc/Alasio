import { t } from "$lib/i18n";

/**
 * Relative time of an instant of the past, e.g. "Just now" / "5m ago" /
 * "3h ago" / "2d ago" / "Long ago".
 *
 * The texts live in the Dashboard module (the dashboard items show the time
 * of their last update with them), every other page borrows them here.
 *
 * Args:
 *   before (Date): The instant to describe
 *   now (number): Current time in milliseconds, e.g. `globalClock.now`
 *
 * Returns:
 *   (str): The relative time, ready to be displayed
 */
export function readableTime(before: Date, now: number): string {
  const diff = (now - before.getTime()) / 1000;

  if (diff < -1) {
    return t.Dashboard.TimeError();
  } else if (diff < 60) {
    // < 1 min
    return t.Dashboard.JustNow();
  } else if (diff < 5400) {
    // < 90 min
    return t.Dashboard.MinutesAgo({ time: Math.floor(diff / 60) });
  } else if (diff < 129600) {
    // < 36 hours
    return t.Dashboard.HoursAgo({ time: Math.floor(diff / 3600) });
  } else if (diff < 7776000) {
    // < 90 days
    return t.Dashboard.DaysAgo({ time: Math.floor(diff / 86400) });
  } else {
    // >= 90 days
    return t.Dashboard.LongTimeAgo();
  }
}
