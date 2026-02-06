<script lang="ts">
  import { cn } from "$lib/utils";
  import type { LogDataProps } from "./types";

  let { t: timestamp, l: level, m: message, e: exception, r: raw }: LogDataProps = $props();

  // Format timestamp to local time
  const date = $derived(new Date(timestamp * 1000));

  // Short format: hh:mm:ss.ms
  const shortTime = $derived.by(() => {
    const hours = date.getHours().toString().padStart(2, "0");
    const minutes = date.getMinutes().toString().padStart(2, "0");
    const seconds = date.getSeconds().toString().padStart(2, "0");
    const ms = date.getMilliseconds().toString().padStart(3, "0");
    return `${hours}:${minutes}:${seconds}.${ms}`;
  });

  // Full format for hover
  const fullTime = $derived(date.toLocaleString());

  // Get level color classes
  const levelClass = $derived.by(() => {
    switch (level) {
      case "DEBUG":
        return "text-muted-foreground";
      case "INFO":
        return "";
      case "WARNING":
        return "bg-yellow-500/20 hover:bg-yellow-500/40 text-yellow-700 dark:text-yellow-400";
      case "ERROR":
      case "CRITICAL":
        return "bg-red-500/20 hover:bg-red-500/40 text-red-700 dark:text-red-400";
      default:
        return "";
    }
  });
</script>

<div
  class={cn(
    "hover:bg-muted/50 hover:shadow-muted-foreground/15 hover:shadow-[inset_0_1px_0_0_currentColor,inset_0_-1px_0_0_currentColor]",
    "flex w-fit min-w-[calc(100cqw)] gap-0.5 px-1 py-0.25 font-mono text-xs",
    levelClass,
  )}
>
  {#if raw}
    <pre class="whitespace-pre">{message}</pre>
  {:else}
    <span class="text-muted-foreground inline-block shrink-0" title={fullTime}>
      {shortTime}
    </span>
    <span class="text-muted-foreground inline-block shrink-0">|</span>
    <!-- 5.87rem(86.7px+7.225px) for time and separator + gap(0.125rem) * 2 + paddingX(0.25rem) * 2 = 6.62rem -->
    <pre class="max-w-[calc(100cqw-6.62rem)] min-w-0 flex-1 break-all whitespace-pre-wrap">{message}</pre>
  {/if}
</div>
{#if exception}
  <pre class="w-fit px-1 font-mono text-xs whitespace-pre">{exception}</pre>
{/if}
