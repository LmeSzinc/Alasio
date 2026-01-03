<script lang="ts">
  import { cn } from "$lib/utils";
  import type { LogDataProps } from "./types";

  let { t, l, m, e }: LogDataProps = $props();

  // Format timestamp to local time
  const date = $derived(new Date(t * 1000));

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
    const level = l.toUpperCase();
    switch (level) {
      case "DEBUG":
        return "text-muted-foreground";
      case "INFO":
        return "";
      case "WARNING":
        return "bg-yellow-500/20 text-yellow-700 dark:text-yellow-400 px-1 rounded";
      case "ERROR":
      case "CRITICAL":
        return "bg-red-500/20 text-red-700 dark:text-red-400 px-1 rounded";
      default:
        return "";
    }
  });
</script>

<div
  class={cn(
    "hover:bg-muted/50 hover:shadow-[inset_0_1px_0_0_currentColor,inset_0_-1px_0_0_currentColor] hover:shadow-muted-foreground/15",
    "font-mono text-xs",
    levelClass,
  )}
>
  <span class="text-muted-foreground inline-block" title={fullTime}>
    {shortTime}
  </span>
  <span class="text-muted-foreground inline-block">|</span>
  <pre class="inline wrap-break-word whitespace-pre-wrap">{m}</pre>
  {#if e}
    <pre class="mt-1 ml-8 wrap-break-word whitespace-pre-wrap text-red-600 dark:text-red-400">{e}</pre>
  {/if}
</div>
