<script lang="ts">
  import { cn } from "$lib/utils";
  import type { ArgData } from "../arg/utils.svelte";

  let {
    data,
    variant = "default",
    class: className,
  }: {
    data: Record<string, ArgData>;
    variant?: "default" | "primary";
    class?: string;
  } = $props();

  const dashboardType = $derived(data._info?.dashboard ?? "");

  const formattedProgress = (val: any) => {
    const num = typeof val === "number" ? val : parseFloat(val);
    if (isNaN(num)) return "0.00";
    return Math.min(100, Math.max(0, num)).toFixed(2);
  };
</script>

{#snippet valueTotal(val: string | number | undefined, denom: string | number | undefined)}
  <span class={cn("font-medium", variant === "primary" && "font-bold")}>
    {val ?? "NaN"}
  </span>
  <span class="text-muted-foreground text-[0.8em]">
    / {denom ?? "NaN"}
  </span>
{/snippet}

<div class={cn("relative flex h-6 min-w-0 flex-row items-baseline gap-1", className)}>
  {#if dashboardType === "Amount"}
    <!-- 8654 -->
    <span class={cn("truncate font-medium", variant === "primary" && "font-bold")}>
      {data.Value?.value ?? "NaN"}
    </span>
  {:else if dashboardType === "Total" || dashboardType === "Remain"}
    <!-- 8000 / 14000 -->
    {@render valueTotal(data.Value?.value, data.Value?.le)}
  {:else if dashboardType === "DynamicTotal"}
    <!-- 8000 / 14000 -->
    {@render valueTotal(data.Value?.value, data.Total?.value)}
  {:else if dashboardType === "Progress"}
    <!-- 86.54% -->
    <span class={cn("font-medium", variant === "primary" && "font-bold")}>
      {formattedProgress(data.Value?.value)}%
    </span>
  {:else if dashboardType === "Planner"}
    <!-- 86.54% >2.3d -->
    <span class={cn("font-medium", variant === "primary" && "font-bold")}>
      {formattedProgress(data.Progress?.value)}%
    </span>
    <span class={cn("text-muted-foreground truncate text-[0.8em]")}>&gt;{data.Eta?.value ?? "NaN"}</span>
  {/if}
</div>
