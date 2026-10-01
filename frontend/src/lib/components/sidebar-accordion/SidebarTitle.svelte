<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils.js";
  import SidebarRowIndicator from "./SidebarRowIndicator.svelte";
  import { sidebarRowClass } from "./row";

  // The title of a section of a sidebar (the DevNav groups its entries that
  // way). It is not a row and it is never current, but it takes the metrics of
  // a row and reserves the select indicator like the trigger of a group does,
  // so a title lines up with the labels of the entries below it.
  //
  // The type scale of the title is the muted label a section header uses (the
  // same as the label of a context menu), which comes after the metrics of a
  // row, and py-2 makes the title as tall as a row: a section header never
  // competes with the entries it groups.
  let {
    class: className,
    children,
  }: {
    class?: string;
    children?: Snippet;
  } = $props();
</script>

<h2 class={cn("flex items-center", sidebarRowClass, "text-muted-foreground py-2 text-xs font-medium", className)}>
  <SidebarRowIndicator />
  {@render children?.()}
</h2>
