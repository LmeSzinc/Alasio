<script lang="ts">
  import { windowFrame } from "$lib/windowFrame.svelte";
  import WindowControls from "./WindowControls.svelte";

  let { floating = false } = $props();

  const px = (value: number | string) => (typeof value === "number" ? `${value}px` : value);

  // The band the frontend reported, or the whole title bar minus the window
  // controls when it reported nothing (the geometry of the flex strip this
  // component used to render).
  const region = $derived(windowFrame.region ?? { height: 48, left: 0, right: windowFrame.controlsWidth });

  // Only the strip itself is a drag area: the rest of the overlay stays
  // pointer-transparent, so clicks reach the embedded page.
  const dragStyle = $derived(
    `left: ${px(region.left)}; right: ${px(region.right)}; height: ${px(region.height)}; -webkit-app-region: drag`,
  );
</script>

{#if floating}
  <!-- Floating mode: the embedded web app provides its own header, so only the
       transparent drag strip and the window controls are overlaid. Where the
       strip may live comes from the page itself (see $lib/windowFrame). -->
  <div class="pointer-events-none fixed inset-x-0 top-0 z-100 h-12 select-none">
    <div class="pointer-events-auto absolute top-0" style={dragStyle}></div>
    <div class="pointer-events-auto absolute top-0 right-0 flex h-12 items-center">
      <WindowControls />
    </div>
  </div>
{:else}
  <!-- Keep h-12 aligned with AppHeader bottom in the embedded web app -->
  <div class="z-100 flex h-12 items-center border-b border-border bg-card select-none">
    <!-- self-stretch makes the drag region span the full 48px title bar height -->
    <div class="flex flex-1 items-center self-stretch px-4" style="-webkit-app-region: drag">
      <span class="text-sm font-semibold">Alasio</span>
    </div>
    <WindowControls />
  </div>
{/if}
