<script lang="ts">
  import { Indicator, type DropIndicatorState } from "$lib/components/dnd";
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import { GripVertical } from "@lucide/svelte";

  export type Config = {
    name: string;
    mod: string;
    gid: number;
    iid: number;
    extra: string;
    id: number;
  };

  type Props = {
    config: Config;
    dropIndicator?: DropIndicatorState | null;
  };
  let { config, dropIndicator = null }: Props = $props();

  const {
    attributes,
    listeners,
    isDragging,
    setNodeRef: setDraggableNode,
  } = useDraggable({
    id: config.name,
    data: { type: "item", config: config, containerId: config.gid },
  });

  const { setNodeRef: setDroppableNode } = useDroppable({
    id: config.name,
    data: { type: "item", config: config, containerId: config.gid },
  });

  // Compute the indicator specifically for this item.
  const indicator = $derived(dropIndicator?.targetId === config.name ? dropIndicator.position : null);
</script>

<div
  use:setDraggableNode
  use:setDroppableNode
  data-dragging={isDragging.current}
  class="drag-placeholder relative rounded-md"
>
  <div class="bg-card text-card-foreground flex h-14 items-center rounded-md border p-2 shadow-sm">
    <div
      {...listeners.current}
      {...attributes.current}
      class="text-muted-foreground flex h-full cursor-grab items-center px-2 active:cursor-grabbing"
    >
      <GripVertical class="h-5 w-5" />
    </div>
    <div class="flex-grow font-mono text-sm">{config.name}</div>
    <div class="bg-secondary text-secondary-foreground ml-4 rounded px-2 py-1 text-xs">
      {config.mod}
    </div>
  </div>
  <!-- Display drop indicator based on props -->
  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>
