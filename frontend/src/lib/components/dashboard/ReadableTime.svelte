<script lang="ts">
  import { t } from "$lib/i18n";
  import { globalClock } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
  import { DEFAULT_TIME_MS } from "./utils";

  let { time, class: className }: { time: string; class?: string } = $props();

  // Subscribe to global clock updates (1s interval)
  globalClock.use();

  /**
   * Convert "2023-08-29 12:30:53" to "3 Minutes Ago"
   */
  function readableTime(before: string): string {
    if (!before) {
      return t.Dashboard.NoData();
    }

    let ti: Date;
    try {
      // Handling "YYYY-MM-DD HH:mm:ss" which Date.parse might not handle everywhere,
      // but typically browsers handle "YYYY-MM-DDTHH:mm:ss" well.
      // Python fromisoformat is quite flexible.
      const normalized = before.replace(" ", "T");
      ti = new Date(normalized);
      if (isNaN(ti.getTime())) {
        return t.Dashboard.TimeError();
      }
    } catch (e) {
      return t.Dashboard.TimeError();
    }

    // Check against DEFAULT_TIME
    if (ti.getTime() === DEFAULT_TIME_MS) {
      return t.Dashboard.NoData();
    }

    const diff = (globalClock.now - ti.getTime()) / 1000;

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

  // Re-calculate the relative time string whenever time prop or clock.now changes
  const displayTime = $derived(readableTime(time));
</script>

<span class={cn("inline-block whitespace-nowrap", className)}>
  {displayTime}
</span>
