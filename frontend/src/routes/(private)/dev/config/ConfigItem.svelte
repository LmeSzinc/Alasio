<script lang="ts">
  import { Indicator, type DropIndicatorState } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import { Copy, GripVertical, Trash2 } from "@lucide/svelte";

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
    onCopy?: (config: Config) => void;
    onDelete?: (config: Config) => void;
  };
  let { config, dropIndicator = null, onCopy, onDelete }: Props = $props();

  const dndData = $derived({
    id: config.id,
    data: { type: "item", config: config },
  });
  // svelte-ignore state_referenced_locally
  const { attributes, listeners, isDragging, setNodeRef: setDraggableNode } = useDraggable(dndData);
  // svelte-ignore state_referenced_locally
  const { setNodeRef: setDroppableNode } = useDroppable(dndData);

  // Compute the indicator specifically for this item.
  const indicator = $derived(dropIndicator?.targetId === config.id ? dropIndicator.position : null);

  // Config management
  function handleCopy(event: Event) {
    event.stopPropagation();
    onCopy?.(config);
  }
  function handleDelete(event: Event) {
    event.stopPropagation();
    onDelete?.(config);
  }
</script>

<div
  use:setDraggableNode
  use:setDroppableNode
  data-dragging={isDragging.current}
  class="drag-placeholder relative rounded-md"
>
  <div
    class="bg-card text-card-foreground group flex h-14 items-center rounded-md border p-2 shadow-sm transition-shadow hover:shadow-md"
  >
    <div
      {...listeners.current}
      {...attributes.current}
      class="text-muted-foreground flex h-full cursor-grab items-center px-2 active:cursor-grabbing"
    >
      <GripVertical class="h-5 w-5" />
    </div>
    <div class="grow font-mono text-sm">{config.name}</div>
    <div class="bg-secondary text-secondary-foreground ml-4 rounded px-2 py-1 text-xs">
      {config.mod}
    </div>

    <!-- Action buttons - visible on hover -->
    <div class="ml-2 flex items-center gap-1">
      <Button variant="ghost" size="sm" class="h-8 w-8 p-0" onclick={handleCopy} title={t.ConfigScan.CopyConfig()}>
        <Copy class="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        class="text-destructive hover:text-destructive h-8 w-8 p-0"
        onclick={handleDelete}
        title={t.ConfigScan.DeleteConfig()}
      >
        <Trash2 class="h-4 w-4" />
      </Button>
    </div>
  </div>
  <!-- Display drop indicator based on props -->
  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>
