<script lang="ts">
  import { cn } from "$lib/utils";
  import type { ArgData } from "../arg/utils.svelte";

  let {
    data,
    variant = "default",
    mutedClass = "text-muted-foreground",
    class: className,
  }: {
    data: Record<string, ArgData>;
    variant?: "default" | "primary";
    /**
     * Color of the secondary half of the value line (`/ total`, `> eta`). The
     * dashboard item turns it into the flash foreground color while the item
     * is highlighted; on its own the value line keeps the muted text color.
     */
    mutedClass?: string;
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
  <span class={cn("max-w-full shrink-0 truncate font-medium", variant === "primary" && "font-bold")}>
    {val ?? "NaN"}
  </span>
  <!--
    The value line degrades like the item info line (see DashboardItem):
      "1234 / 14000" -> "1234 / 1..." -> "1234" -> "123..."
    The value is the primary text and does not yield (`max-w-full shrink-0`),
    the denominator is the tail and gives up its room first. The tail goes as a
    whole, it is never left behind as a bare separator: `basis-[2.5em]` is the
    width it is worth showing from (separator, one character and the ellipsis),
    so a shorter tail wraps onto the clipped second line instead of rendering
    "…". "12.. / ..." must never show up: both halves would carry no
    information while either of them alone still would.
  -->
  <span
    class={cn("max-w-max min-w-0 grow basis-[2.5em] truncate text-[0.8em] transition-colors duration-200", mutedClass)}
  >
    / {denom ?? "NaN"}
  </span>
{/snippet}

<!--
  One line high: the value row is a fixed `h-6`, and `overflow-hidden` clips the
  second flex line that the tail wraps onto when it runs out of room (see the
  tail snippet above). The text itself never wraps, every span truncates.
-->
<div
  class={cn(
    "relative flex h-6 min-w-0 flex-row flex-wrap content-start items-baseline gap-1 overflow-hidden",
    className,
  )}
>
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
    <span class={cn("truncate font-medium", variant === "primary" && "font-bold")}>
      {formattedProgress(data.Value?.value)}%
    </span>
  {:else if dashboardType === "Planner"}
    <!-- 86.54% >2.3d -->
    <span class={cn("max-w-full shrink-0 truncate font-medium", variant === "primary" && "font-bold")}>
      {formattedProgress(data.Progress?.value)}%
    </span>
    <span
      class={cn(
        "max-w-max min-w-0 grow basis-[2.5em] truncate text-[0.8em] transition-colors duration-200",
        mutedClass,
      )}>&gt;{data.Eta?.value ?? "NaN"}</span
    >
  {/if}
</div>
