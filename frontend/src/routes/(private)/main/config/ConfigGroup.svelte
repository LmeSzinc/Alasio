<script lang="ts">
  import { Indicator } from "$lib/components/dnd";
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import { GripVertical } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import type { Config } from "./ConfigItem.svelte";

  type ConfigGroupData = {
    id: number;
    items: Config[];
  };

  type Props = {
    group: ConfigGroupData;
    children: Snippet;
    indicator?: "before" | "after" | null;
  };
  let { group, children, indicator = null }: Props = $props();

  const { isOver, setNodeRef: setDroppableNode } = useDroppable({
    id: group.id,
    data: { type: "group" },
  });

  const { attributes, listeners, isDragging, setNodeRef: setDraggableNode } = useDraggable({
    id: group.id,
    data: { type: "group", group: group },
  });
</script>

<div use:setDraggableNode data-dragging={isDragging.current} class="drag-placeholder relative rounded-lg">
  <div class="rounded-lg">
    <Card.Root>
      <Card.Header class="flex flex-row items-center justify-between px-4 py-3">
        <Card.Title class="text-lg">Group {group.id}</Card.Title>
        <div
          {...listeners.current as any}
          {...attributes.current}
          class="text-muted-foreground cursor-grab p-2 active:cursor-grabbing"
        >
          <GripVertical class="h-5 w-5" />
        </div>
      </Card.Header>
      <div use:setDroppableNode>
        <Card.Content class={cn("space-y-1 p-4 pt-0 transition-colors", { "bg-primary/5": isOver.current })}>
          {#if group.items.length > 0}
            {@render children()}
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
  {#if indicator === "before"}
    <Indicator edge="top" />
  {:else if indicator === "after"}
    <Indicator edge="bottom" />
  {/if}
</div>
