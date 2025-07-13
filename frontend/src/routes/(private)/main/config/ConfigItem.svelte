<script lang="ts">
  import Indicator from "$lib/components/dnd/Indicator.svelte";
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
    indicator?: "before" | "after" | null;
  };
  let { config, indicator = null }: Props = $props();

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
</script>

<div use:setDraggableNode use:setDroppableNode data-dragging={isDragging.current} class="relative rounded-md">
  <!-- The real content is the only direct child -->
  <div class="bg-card text-card-foreground flex h-14 items-center rounded-md border p-2 shadow-sm">
    <div
      {...listeners.current as any}
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
  {#if indicator === "before"}
    <Indicator edge="top" />
  {:else if indicator == "after"}
    <Indicator edge="bottom" />
  {/if}
</div>

<style>
  /* When dragging, hide the real content */
  [data-dragging="true"] > :first-child {
    visibility: hidden;
  }

  /* And show the ::before pseudo-element as a placeholder */
  [data-dragging="true"]::before {
    content: "";
    position: absolute;
    inset: 0; /* Fills the parent div completely */
    border-radius: inherit; /* Inherits the parent's rounded-md */
    border-width: 2px;
    border-style: dashed;
    border-color: hsl(var(--primary) / 0.5);
    background-color: hsl(var(--primary) / 0.1);
  }
</style>
