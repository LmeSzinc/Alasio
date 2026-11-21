<script lang="ts">
  import { fullTime, shortTime } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
  import type { LogDataProps } from "./types";

  let { t: timestamp, l: level, m: message, e: exception, r: raw }: LogDataProps = $props();

  // Format timestamp to local time
  const ms = $derived(timestamp * 1000);
  const formattedShortTime = $derived(shortTime(ms));
  const formattedFullTime = $derived(fullTime(ms));

  // Get level color classes
  const levelClass = $derived.by(() => {
    switch (level) {
      case "DEBUG":
        return "text-muted-foreground";
      case "INFO":
        return "text-foreground";
      case "WARNING":
        return "bg-yellow-500/15 hover:bg-yellow-500/30 text-yellow-700 dark:text-yellow-400";
      case "ERROR":
        return "bg-red-500/15 hover:bg-red-500/30 text-red-700 dark:text-red-400";
      case "CRITICAL":
        return "bg-red-500/15 hover:bg-red-500/30 font-bold text-red-700 dark:text-red-400";
      default:
        return "";
    }
  });
  const timeClass = $derived.by(() => {
    switch (level) {
      case "DEBUG":
      case "INFO":
        return "text-muted-foreground";
      default:
        return "";
    }
  });
</script>

<div
  class={cn(
    "hover:bg-muted/50 hover:shadow-[inset_0_1px_0_0_currentColor,inset_0_-1px_0_0_currentColor] hover:shadow-muted-foreground/15",
    // Breathing room around the text comes from the line height (`text-xs` is
    // 1rem, a row is 1.125rem here), not from vertical padding. Padding adds the
    // same 2px to an entry of any line count, which walks every entry off the
    // row grid the log viewer fits its viewport to; the line height keeps every
    // entry a whole number of rows (see LogDisplay.svelte).
    "block max-w-[calc(100cqw)] min-w-[calc(100cqw)] px-1 font-mono text-xs leading-[1.125rem]",
    levelClass,
  )}
>
  {#if raw}
    <pre class="whitespace-pre">{message}</pre>
  {:else}
    <!-- Hanging indent calculation: (Time: 12ch=86.7px) + (Separator visual: 1ch=7.225px) + (Gap: 2 * 0.25rem = 8px) = 101.925px = 6.37rem -->
    <pre class="m-0 pl-[6.37rem] -indent-[6.37rem] break-all whitespace-pre-wrap"><span
        class={cn("inline", timeClass)}
        title={formattedFullTime}>{formattedShortTime}</span
      ><span
        class={cn("mx-1 inline-flex w-[1ch] overflow-hidden", timeClass)}
        style="text-indent: -1ch"> | </span>{message}</pre>
  {/if}
</div>
{#if exception}
  <!-- Same row as an entry: the same leading, and the default pre margin removed -->
  <pre class="m-0 w-fit px-1 font-mono text-xs leading-[1.125rem] whitespace-pre">{exception}</pre>
{/if}
