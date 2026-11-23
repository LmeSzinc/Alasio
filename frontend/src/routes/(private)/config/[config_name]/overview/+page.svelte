<script lang="ts">
  import { elementSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import Dashboard from "./Dashboard.svelte";
  import Log from "./Log.svelte";
  import Preview from "./Preview.svelte";
  import Progress from "./Progress.svelte";

  let { data } = $props();
  const config_name = $derived(data.config_name);

  const containerSize = $state({ width: 0, height: 0 });
  const isLandscape = $derived(containerSize.width / containerSize.height >= 1.2);

  // The box the cards are stacked in, measured on the stack itself. Its content
  // box is the height the cards actually have: the browser already took the
  // page padding off, so the padding never appears in the numbers below, and
  // chrome added around the page follows the budget without an edit here.
  const stackSize = $state({ width: 0, height: 0 });

  // Tailwind spacing unit: 1 = 0.25rem = 4px (the `--spacing` default)
  const SPACING = 4;

  // Spacing follows the size of the page, not the orientation of the layout.
  // The page turns landscape exactly when it gets shorter, so tying the roomy
  // spacing to the orientation would grow the padding on a shrinking page.
  // The roomy p-4 / gap-4 needs room in both directions: the page box is at
  // least 640px on each axis (the `sm` breakpoint); below that on either one
  // the page is compact (p-2.5 / gap-2.5) and the space goes to the cards.
  const ROOMY_MIN_WIDTH = 640;
  const ROOMY_MIN_HEIGHT = 640;
  const isCompact = $derived(containerSize.width < ROOMY_MIN_WIDTH || containerSize.height < ROOMY_MIN_HEIGHT);

  // Sizes inside the landscape column, in the step counts of the Tailwind
  // classes the markup applies. These are the only numbers the height budget
  // needs, so each one names the class it mirrors:
  // - min-h-35: the preview and the dashboard each keep this minimum. The
  //   preview is the page's content, so it is the last thing to give height
  //   up, and the floor is enough to show a frame rather than a strip
  // - h-20: the progress bar
  // - gap-2.5: between the preview and the progress bar
  // - gap-4 (gap-2.5 on a compact page): between the blocks of the column
  const CARD_MIN_HEIGHT = 35 * SPACING;
  const PROGRESS_HEIGHT = 20 * SPACING;
  const PREVIEW_PROGRESS_GAP = 2.5 * SPACING;
  const BLOCK_GAP = $derived((isCompact ? 2.5 : 4) * SPACING);

  /** Height the landscape column has, as the stack reports it */
  const columnHeight = $derived(stackSize.height);

  // Landscape only: the cards are dropped in order of importance once the
  // column cannot hold them at their minimum heights -- the progress bar (a
  // placeholder, its height is worth more to the cards below) first, then the
  // dashboard. A hidden card leaves the layout and hands its height to the
  // cards that remain, so the preview grows through both stages. The decision
  // reads the measured height only: it must not follow the cards it hides, or
  // hiding one would make room, show it again, and the page would oscillate.
  const hideProgress = $derived(
    isLandscape && columnHeight < CARD_MIN_HEIGHT * 2 + PREVIEW_PROGRESS_GAP + PROGRESS_HEIGHT + BLOCK_GAP,
  );
  // The preview keeps the whole column once the dashboard no longer fits
  const hideDashboard = $derived(isLandscape && columnHeight < CARD_MIN_HEIGHT * 2 + BLOCK_GAP);
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

       Constraints, in the order the height is given up when the window gets
       short:
       - Preview: aspect-video (16/9), flex-shrink: 1, min-h-35
         -> shrinks down to that minimum, image object-contain for letterboxing
       - Progress: height: 80px (h-20), flex-shrink: 0
         -> fixed height, never shrinks; hidden once the column cannot hold the
            preview minimum, the progress bar and the dashboard minimum plus
            the gaps between them, so its height goes to the preview and the
            dashboard
       - Dashboard: flex: 1 0 0%, min-h-35
         -> fills remaining height; never shorter than two whole dashboard
            items (item 54px + row gap 8px + padding 24px), so a squeezed
            window shows whole items instead of a sliver at the card edge
       - When even the preview minimum and the dashboard minimum no longer
         fit, the dashboard is hidden as well: the preview takes the whole
         column and stretches the frame over it (object-fill), because the
         frame ratio no longer matches the box

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
       - Progress: flex: 1 0 0%, min-h-15
         -> fills remaining after Preview; when min-height is hit, left-col shrinks -> Preview squeezed
       - Dashboard: flex: 1 -- equal half top-row width
       - Log: flex: 1 -- bottom half

       Spacing: the page padding and the gap between the blocks follow the size
       of the page, not the arrangement, so a smaller page is never padded more
       than a larger one: p-4 / gap-4 while the page box is at least 640px in
       both directions, p-2.5 / gap-2.5 below that on either one.

       Height budget: the cards are dropped by the height of the stack below,
       measured on the stack itself, so the page padding never enters the
       numbers (the browser already took it off the content box).
  -->
  <div
    class={cn("flex h-full w-full", isLandscape ? "flex-row" : "flex-col", isCompact ? "gap-2.5 p-2.5" : "gap-4 p-4")}
    use:elementSize={stackSize}
  >
    <!--
      Wraps Preview+Progress (left-col) and Dashboard.
      Landscape: flex-col -> left-col above, Dashboard below (fills remaining).
      Portrait:  flex-row -> left-col left, Dashboard right (equal half width).
    -->
    <div
      class={cn("flex min-h-0 min-w-0 flex-1", isLandscape ? "flex-col" : "flex-row", isCompact ? "gap-2.5" : "gap-4")}
    >
      <!-- Preview + Progress column -->
      <div class={cn("flex min-h-0 min-w-0 flex-col gap-2.5", !isLandscape || hideDashboard ? "flex-1" : "")}>
        <!--
          Preview: aspect-video down to its minimum height while a card is
          stacked under it in landscape; fills the column when the dashboard is
          hidden. min-h-35 is the CARD_MIN_HEIGHT step count of the height
          budget above.
        -->
        <div
          class={cn(
            "w-full",
            hideDashboard ? "min-h-0 flex-1" : "aspect-video shrink",
            isLandscape && !hideDashboard && "min-h-35",
          )}
        >
          <Preview {config_name} class="h-full w-full" stretch={hideDashboard} />
        </div>
        {#if !hideProgress}
          <div class={cn("w-full shrink-0", isLandscape ? "h-20" : "min-h-15 flex-1")}>
            <Progress class="h-full w-full" />
          </div>
        {/if}
      </div>
      <!--
        Dashboard:
        Landscape: flex:1 0 0% (fills remaining height in column, won't shrink below min-height)
        Portrait:  flex:1 (equal half width in row)
      -->
      {#if !hideDashboard}
        <div class={cn("min-h-0 min-w-0", isLandscape ? "min-h-35 shrink-0 grow basis-0" : "flex-1")}>
          <Dashboard class="h-full w-full" />
        </div>
      {/if}
    </div>
    <!-- Log: always flex-1 alongside the middle wrapper -->
    <div class="min-h-0 min-w-0 flex-1">
      <Log class="h-full w-full" />
    </div>
  </div>
</div>
