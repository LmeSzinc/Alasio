<script lang="ts">
  import { Indicator, type DropIndicatorState } from "$lib/components/dnd";
  import { t } from "$lib/i18n";
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
    onCopy?: (config: Config) => void;
    onRename?: (config: Config) => void;
    onDelete?: (config: Config) => void;
  };
  let { group, dropIndicator = null, onCopy, onRename, onDelete }: Props = $props();

  const dndData = $derived({
    id: group.id,
    data: { type: "group", group: group },
  });
  // svelte-ignore state_referenced_locally
  const { isOver, setNodeRef: setDroppableNode } = useDroppable(dndData);
  // svelte-ignore state_referenced_locally
  const { attributes, listeners, isDragging, setNodeRef: setDraggableNode } = useDraggable(dndData);

  // Compute the indicator specifically for this group.
  const indicator = $derived(dropIndicator?.targetId === group.id ? dropIndicator.position : null);
</script>

<div use:setDraggableNode use:setDroppableNode data-dragging={isDragging.current} class="drag-placeholder relative">
  <div class="group-container relative rounded-lg p-3 pt-4">
    <div class="absolute -top-2 left-6 z-2 flex items-center">
      <!-- icon and text cover the border -->
      <span class="text-muted-foreground bg-background pl-2 text-xs">
        {t.ConfigScan.SchedulerGroup({ n: group.gid })}
      </span>
      <div
        {...listeners.current}
        {...attributes.current}
        class="text-muted-foreground bg-background cursor-grab px-2 py-1 active:cursor-grabbing"
      >
        <GripVertical class="h-3 w-3" />
      </div>
    </div>

    <div class={cn("space-y-1 transition-colors", { "bg-accent/30 rounded-md": isOver.current })}>
      {#if group.items.length > 0}
        {#each group.items as item (item.id)}
          <ConfigItem config={item} {dropIndicator} {onCopy} {onRename} {onDelete} />
        {/each}
      {:else}
        <div
          class="text-muted-foreground flex h-20 items-center justify-center rounded-md border border-dashed text-sm"
        >
          Drop items here
        </div>
      {/if}
    </div>
  </div>

  <!-- Display drop indicator based on props -->
  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>

<style>
  /* dashed border */
  .group-container::before {
    content: "";
    position: absolute;
    inset: 0;
    border: 2px dashed var(--border);
    border-radius: 0.625rem;
    pointer-events: none;
  }
</style>
