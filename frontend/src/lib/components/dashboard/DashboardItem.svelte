<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName } from "../arg/utils.svelte";
  import ColorDot from "./ColorDot.svelte";
  import ReadableTime from "./ReadableTime.svelte";
  import type { DashboardGroupData, DashboardGroupInfo } from "./utils";

  let { data, class: className }: { data: DashboardGroupData; class?: string } = $props();

  const info = $derived(data._info ?? {}) as DashboardGroupInfo;
  const dashboardType = $derived(info.dashboard ?? "");
  const time = $derived(data.Time?.value ?? "");

  // Format progress to percentage like 86.54
  const formattedProgress = (val: any) => {
    const num = typeof val === "number" ? val : parseFloat(val);
    if (isNaN(num)) return "0.00";
    return Math.min(100, Math.max(0, num)).toFixed(2);
  };
</script>

{#snippet valueTotal(value: string | number | undefined, total: string | number | undefined)}
  <!-- 8000 / 14000 -->
  <span class="font-medium">
    {value ?? "NaN"}
  </span>
  <span class="text-muted-foreground text-[0.8em]">
    / {total ?? "NaN"}
  </span>
{/snippet}

<div class={cn("flex w-42 shrink-0 items-stretch gap-2.5 overflow-hidden pl-2", className)}>
  <div class="mt-2">
    <ColorDot color={data._info?.dashboard_color ?? "#777"} class="h-2 w-2 rounded-full" />
  </div>

  <div class="flex min-w-0 flex-1 flex-col gap-0.5">
    <!-- Pretty Value -->
    <div class={cn("relative flex h-6 min-w-0 flex-row items-baseline gap-1", className)}>
      {#if dashboardType === "Amount"}
        <!-- 8654 -->
        <span class="truncate font-medium">
          {data.Value?.value ?? "NaN"}
        </span>
      {:else if dashboardType === "Total"}
        <!-- 8000 / 14000 -->
        {@render valueTotal(data.Value?.value, data.Value?.le)}
      {:else if dashboardType === "DynamicTotal"}
        <!-- 8000 / 14000 -->
        {@render valueTotal(data.Value?.value, data.Total?.value)}
      {:else if dashboardType === "Progress"}
        <!-- 86.54% -->
        <span class="font-medium">
          {formattedProgress(data.Value?.value)}%
        </span>
      {:else if dashboardType === "Planner"}
        <!-- 86.54% >2.3d -->
        <span class="font-medium">
          {formattedProgress(data.Progress?.value)}%
        </span>
        <span class="text-muted-foreground truncate text-[0.8em]">&gt;{data.Eta?.value ?? "NaN"}</span>
      {/if}
    </div>

    <!-- Info -->
    <!-- Arg Name - Time -->
    <div class="text-muted-foreground flex items-center gap-0.5 text-xs whitespace-nowrap">
      <span class="truncate" title={getArgName(info)}>
        {getArgName(info)}
      </span>
      <span class="shrink-0 opacity-50">-</span>
      <ReadableTime {time} class="" />
    </div>
  </div>
</div>
