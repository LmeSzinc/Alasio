<script lang="ts">
  import { Indicator, type DropIndicatorState } from "$lib/components/dnd";
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import { GripVertical } from "@lucide/svelte";
  import ConfigItem, { type Config } from "./ConfigItem.svelte";

  export type ConfigGroupData = {
    id: string;
    gid: number;
    items: Config[];
  };

  type Props = {
    group: ConfigGroupData;
    dropIndicator?: DropIndicatorState | null;
  };
  let { group, dropIndicator = null }: Props = $props();

  const dndData = {
    id: group.id,
    data: { type: "group", group: group },
  };
  const { isOver, setNodeRef: setDroppableNode } = useDroppable(dndData);
  const { attributes, listeners, isDragging, setNodeRef: setDraggableNode } = useDraggable(dndData);

  // Compute the indicator specifically for this group.
  const indicator = $derived(dropIndicator?.targetId === group.id ? dropIndicator.position : null);
</script>

<div use:setDraggableNode data-dragging={isDragging.current} class="drag-placeholder relative rounded-lg">
  <div class="rounded-lg">
    <Card.Root>
      <Card.Header class="flex flex-row items-center justify-between px-4 py-3">
        <Card.Title class="text-lg">Group {group.gid}</Card.Title>
        <div
          {...listeners.current}
          {...attributes.current}
          class="text-muted-foreground cursor-grab p-2 active:cursor-grabbing"
        >
          <GripVertical class="h-5 w-5" />
        </div>
      </Card.Header>
      <div use:setDroppableNode>
        <Card.Content class={cn("space-y-1 p-4 pt-0 transition-colors", { "bg-primary/5": isOver.current })}>
          {#if group.items.length > 0}
            <!-- Pass the indicator state down to the children -->
            {#each group.items as item (item.id)}
              <ConfigItem config={item} {dropIndicator} />
            {/each}
          {:else}
            <div
              class="text-muted-foreground flex h-14 items-center justify-center rounded-md border border-dashed text-sm"
            >
              Drop items here
            </div>
          {/if}
        </Card.Content>
      </div>
    </Card.Root>
  </div>
  <!-- Display drop indicator based on props -->
  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>
