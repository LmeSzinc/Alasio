<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { HEADER_PADDING_X } from "$lib/components/header/geometry";
  import ThemeToggle from "$lib/components/ui/theme/theme-toggle.svelte";
  import LangSelector from "$lib/i18n/LangSelector.svelte";
  import { cn } from "$lib/utils.js";
  import SimulatedWindowControls from "$lib/window/SimulatedWindowControls.svelte";
  import { frame } from "$lib/window/frame.svelte";

  // props
  type $props = {
    children?: Snippet;
    class?: string;
  };
  let { children, class: className }: HTMLAttributes<HTMLHeadElement> & $props = $props();

  // Reserve the host window controls on top of the normal padding. `px-4` sets
  // both sides, so writing the bare controls width here would replace the 16px
  // instead of adding to it, and the trailing actions would sit 16px further
  // from the drag band than in a session without controls.
  const reserveRight = $derived(frame.controls.width + HEADER_PADDING_X);

  // A browser has no host to draw the window controls, so the reserved space is
  // filled with inert stand-ins; a real client draws its own on top of this
  // header and must not get a duplicate set.
  const showSimulatedControls = $derived(frame.controls.source === "simulated");
</script>

<header
  class={cn(
    // global
    "app-header relative z-40",
    "bg-card flex h-12 w-full items-center gap-1 px-4",
    className,
  )}
  style={`padding-right: ${reserveRight}px`}
>
  <!-- header content -->
  {@render children?.()}

  <LangSelector></LangSelector>
  <ThemeToggle variant="ghost"></ThemeToggle>

  {#if showSimulatedControls}
    <SimulatedWindowControls />
  {/if}
</header>
