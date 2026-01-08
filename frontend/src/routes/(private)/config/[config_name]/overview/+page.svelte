<script lang="ts">
  import { sizeObserver } from "$lib/use/size.svelte";
  import Dashboard from "./Dashboard.svelte";
  import Log from "./Log.svelte";
  import Progress from "./Progress.svelte";
  import Screenshot from "./Screenshot.svelte";
  import { cn } from "$lib/utils";

  const containerSize = $state({ width: 0, height: 0 });
  const isWide = $derived(containerSize.width > 800);
</script>

<!-- A wrapper div to listen container size that doesnot affect by dynamic padding-->
<div class="w-full h-full" use:sizeObserver={containerSize}>
  <div class={cn("flex h-full w-full flex-col overflow-visible", "", isWide ? "gap-4 p-4" : "gap-2.5 p-2.5")}>
    <Dashboard class="h-28 w-full shrink-0" />
    <div class={cn("flex min-h-0 flex-1", isWide ? "gap-4" : "flex-col gap-2.5")}>
      <div class={cn("flex shrink-0", isWide ? "flex-1 flex-col gap-4" : "w-full gap-2.5")}>
        <Screenshot class={cn("w-full", isWide ? "min-h-60" : "w-3/5")} />
        <Progress class="w-full flex-1" />
      </div>
      <Log class="min-h-0 flex-1" />
    </div>
  </div>
</div>
