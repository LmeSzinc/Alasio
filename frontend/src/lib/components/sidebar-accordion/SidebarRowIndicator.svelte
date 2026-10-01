<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { sidebarRowIndicatorClass } from "./row";

  // The select indicator of a row, the bar before its label: it is painted
  // when the row is the current one, and transparent otherwise.
  //
  // Every row of the family mounts it, including the rows that can never be
  // current, so the space before the label is the same everywhere and the
  // labels of a nav stay left aligned whatever the kind of the row is.
  let {
    active = false,
    class: className,
  }: {
    active?: boolean;
    class?: string;
  } = $props();
</script>

<div class={cn(sidebarRowIndicatorClass, active ? "bg-primary" : "bg-transparent", className)} aria-hidden="true"></div>
