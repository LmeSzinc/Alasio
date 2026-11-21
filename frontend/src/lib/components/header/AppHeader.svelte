<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { headerPaddingRight } from "$lib/components/header/geometry";
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

  // The reserve width already includes the gap between the content and the
  // buttons, so it replaces the `px-4` padding rather than adding to it; a
  // browser without a host keeps the plain padding.
  const reserveRight = $derived(headerPaddingRight(frame.controls.width));

  // A browser has no host to draw the window controls, so the reserved space is
  // filled with inert stand-ins; a real client draws its own on top of this
  // header and must not get a duplicate set.
  const showSimulatedControls = $derived(frame.controls.source === "simulated");
</script>

<header
  class={cn(
    // global
    "app-header relative z-40",
    "flex h-12 w-full items-center gap-1 bg-card px-4",
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
