<script lang="ts">
  import { useDroppable } from "@dnd-kit-svelte/core";
  import type { Snippet } from "svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";

  /**
   * A scrollable column of the filter-order editor, the droppable container of
   * its rows.
   *
   * The component is needed because the droppable must be registered while its
   * node is in the DOM: the dialog itself is mounted from the start and only
   * renders its content when it opens, so a container droppable owned by the
   * dialog would register a null node and never be measured. This component
   * mounts together with the dialog content, like the rows do.
   *
   * The droppable is the wrapper around the scroll port, so its rect is the
   * visible column and not the (taller) scrolled content. The container
   * accepts a drop on the empty area and on the gaps between the rows; `id` is
   * stable (`filter-order-{column}`), the drop handler maps it to "insert
   * first" (top area) or "append last" (the rest).
   *
   * The port is exposed as `viewport` so the editor can bring a row into view:
   * a row the user just added lands at the end of a long list, which the port
   * may have scrolled far away from.
   */
  type Props = {
    /** Which column this is, it decides the droppable type of the container */
    column: "selected" | "unused";
    /** The scroll port of the column, bound by the editor that scrolls it */
    viewport?: HTMLElement | null;
    children: Snippet;
  };
  let { column, viewport = $bindable(null), children }: Props = $props();

  // svelte-ignore state_referenced_locally
  const { isOver, setNodeRef } = useDroppable({
    id: `filter-order-${column}`,
    data: { type: column, container: true },
  });
</script>

<div
  use:setNodeRef
  data-slot="filter-order-list"
  data-column={column}
  class={cn("relative min-h-0 flex-1 rounded-md", isOver.current && "bg-accent/30")}
>
  <ScrollArea class="h-full" bind:viewportRef={viewport}>
    <!-- The right padding keeps the item text out of the scrollbar -->
    <div class="flex flex-col gap-y-1 p-0.5 pr-2.5">
      {@render children()}
    </div>
  </ScrollArea>
</div>
