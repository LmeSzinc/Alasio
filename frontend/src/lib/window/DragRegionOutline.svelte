<script lang="ts">
  import { dragRegionOutline, dragRegionState } from "./dragRegion.svelte";

  // Dev-only overlay: draws the band the host would make draggable. In
  // `vite dev` there is no host, so this is the only way to see the geometry
  // (and the effect of a page's declaration) without the electron client.
  const px = (value: number | string) => (typeof value === "number" ? `${value}px` : value);
</script>

{#if dragRegionOutline.enabled && dragRegionState.current}
  {@const region = dragRegionState.current}
  <div
    class="pointer-events-none fixed top-0 z-[9999] border-2 border-dashed border-destructive/70 bg-destructive/5"
    style="left: {px(region.left)}; right: {px(region.right)}; height: {px(region.height)};"
  >
    <span class="absolute bottom-0 left-1 text-[10px] leading-none text-destructive opacity-80">
      {region.left} / {region.right} / {region.height}
    </span>
  </div>
{/if}
