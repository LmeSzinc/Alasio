<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils.js";

  // The container of a group of rows of a sidebar (see SidebarRow): the rows of
  // a section of the DevNav, or of any list that is not inside an accordion.
  //
  // It takes the group padding of the family and lays the rows out in a column,
  // which is what a row counts on: a row is not full width by itself, the list
  // it sits in stretches it and the margins of the metrics inset it.
  let {
    class: className,
    children,
  }: {
    class?: string;
    children?: Snippet;
  } = $props();
</script>

<div class={cn("flex flex-col py-2", className)}>
  {@render children?.()}
</div>
