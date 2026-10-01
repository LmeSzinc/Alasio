<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils.js";
  import SidebarRowIndicator from "./SidebarRowIndicator.svelte";
  import { sidebarRowClass } from "./row";

  // The header of a sidebar section: the title the DevNav opens with, the loud
  // end of the scale next to SidebarTitle. A header introduces the sidebar, a
  // title only groups a few entries under it.
  //
  // Like SidebarTitle it takes the metrics of a row and reserves the select
  // indicator like the trigger of a group does, so a header lines up with the
  // labels of the entries below it.
  let {
    class: className,
    children,
  }: {
    class?: string;
    children?: Snippet;
  } = $props();
</script>

<h2 class={cn("flex items-center", sidebarRowClass, "text-lg font-semibold", className)}>
  <SidebarRowIndicator />
  {@render children?.()}
</h2>
