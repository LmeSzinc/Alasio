<script lang="ts">
  import { cn } from "$lib/utils";
  import type { DashboardArgData } from "./utils";

  let { data, class: className }: { data: DashboardArgData; class?: string } = $props();

  const formattedProgress = (val: any) => {
    const num = typeof val === "number" ? val : parseFloat(val);
    if (isNaN(num)) return "0.00";
    return Math.min(100, Math.max(0, num)).toFixed(2);
  };
</script>

<div class={cn("relative flex h-6 min-w-0 flex-row items-baseline gap-1", className)}>
  {#if data.dt === "dashboard-value"}
    <span class="truncate font-medium">
      {data.value?.Value?.value ?? "NaN"}
    </span>
  {:else if data.dt === "dashboard-total"}
    <span class="font-medium">
      {data.value?.Value?.value ?? "NaN"}
    </span>
    <span class="text-muted-foreground text-[0.8em]">
      / {data.value?.Total?.value ?? "NaN"}
    </span>
  {:else if data.dt === "dashboard-progress"}
    <span class="font-medium">
      {formattedProgress(data.value?.Value?.value)}%
    </span>
  {:else if data.dt === "dashboard-planner"}
    <span class="font-medium">
      {formattedProgress(data.value?.Progress?.value)}%
    </span>
    <span class="text-muted-foreground text-[0.8em] truncate">&gt;{data.value?.Eta?.value ?? "NaN"}</span>
  {/if}
</div>
