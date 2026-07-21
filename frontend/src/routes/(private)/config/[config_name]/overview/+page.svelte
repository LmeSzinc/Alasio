<script lang="ts">
  import { elementSize } from "$lib/use/size.svelte";
  import Dashboard from "./Dashboard.svelte";
  import Log from "./Log.svelte";
  import Progress from "./Progress.svelte";
  import Preview from "./Preview.svelte";
  import { cn } from "$lib/utils";

  let { data } = $props();
  const config_name = $derived(data.config_name);

  const containerSize = $state({ width: 0, height: 0 });
  const isLandscape = $derived(containerSize.width / containerSize.height >= 1.2);
</script>

<!-- A wrapper div to listen container size that does not affect by dynamic padding -->
<div class="h-full w-full" use:elementSize={containerSize}>
  <!-- ========================================================================
       Landscape (width/height >= 1.2) -- left-right split, equal half width

       +--------------------+---------------+
       | Preview            |  Log          |
       | Progress           |               |
       | Dashboard          |               |
       +--------------------+---------------+

       Constraints:
       - Preview: aspect-video (16/9), flex-shrink: 1
         -> shrinks when space tight, image object-contain for letterboxing
       - Progress: height: 80px, flex-shrink: 0
         -> fixed height, never shrinks
       - Dashboard: flex: 1 0 0%, min-height: 100px
         -> fills remaining height; when min-height is hit, left-col shrinks -> Preview squeezed

       ========================================================================
       Portrait (width/height < 1.2) -- top-bottom split

       +------------------+----------------+
       | Preview          |  Dashboard     |
       | Progress         |                |
       +------------------+----------------+
       | Log                               |
       +-----------------------------------+

       Constraints:
       - Preview: aspect-video (16/9), flex-shrink: 1
         -> shrinks when space tight, image object-contain for letterboxing
       - Progress: flex: 1 0 0%, min-height: 60px
         -> fills remaining after Preview; when min-height is hit, left-col shrinks -> Preview squeezed
       - Dashboard: flex: 1 -- equal half top-row width
       - Log: flex: 1 -- bottom half
  -->
  <div class={cn("flex h-full w-full", isLandscape ? "flex-row gap-4 p-4" : "flex-col gap-2.5 p-2.5")}>
    <!--
      Wraps Preview+Progress (left-col) and Dashboard.
      Landscape: flex-col -> left-col above, Dashboard below (fills remaining).
      Portrait:  flex-row -> left-col left, Dashboard right (equal half width).
    -->
    <div class={cn("flex flex-1 min-h-0 min-w-0", isLandscape ? "flex-col gap-4" : "flex-row gap-2.5")}>
      <!-- Preview + Progress column -->
      <div class={cn("flex flex-col min-h-0 min-w-0 gap-2.5", isLandscape ? "" : "flex-1")}>
        <div class="w-full shrink aspect-video">
          <Preview {config_name} class="h-full w-full" />
        </div>
        <div class={cn("w-full shrink-0 min-h-[60px]", isLandscape ? "h-20" : "flex-1")}>
          <Progress class="h-full w-full" />
        </div>
      </div>
      <!--
        Dashboard:
        Landscape: flex:1 0 0% (fills remaining height in column, won't shrink below min-height)
        Portrait:  flex:1 (equal half width in row)
      -->
      <div class={cn("min-h-0 min-w-0", isLandscape ? "grow shrink-0 basis-0 min-h-[100px]" : "flex-1")}>
        <Dashboard class="h-full w-full" />
      </div>
    </div>
    <!-- Log: always flex-1 alongside the middle wrapper -->
    <div class="flex-1 min-h-0 min-w-0">
      <Log class="h-full w-full" />
    </div>
  </div>
</div>
