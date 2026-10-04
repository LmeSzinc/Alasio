<script lang="ts">
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import ArrowDown from "@lucide/svelte/icons/arrow-down";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { type DropIndicatorState, Indicator } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";

  /**
   * One row of the filter-order editor, a draggable and droppable item.
   *
   * The component is needed because the dnd hooks must run at component init,
   * one instance per item: the two columns share this row, `column` decides
   * the buttons and the droppable type. Only the grip is a drag hotspot, the
   * row body itself stays inert.
   */
  type Props = {
    /** The item value, unique over both columns together */
    item: string;
    /** Which column the row lives in */
    column: "selected" | "unused";
    /** Label of the item, translated through option_i18n by the caller */
    label: string;
    /** 1-based position in the selected column, unused for the unused column */
    index?: number;
    /** Length of the selected column, for the down button of the last row */
    count?: number;
    /** True for an item that is not in "option" anymore (an old value) */
    invalid?: boolean;
    dropIndicator?: DropIndicatorState | null;
    onMoveUp?: () => void;
    onMoveDown?: () => void;
    onRemove?: () => void;
    onAdd?: () => void;
  };
  let {
    item,
    column,
    label,
    index = 0,
    count = 0,
    invalid = false,
    dropIndicator = null,
    onMoveUp,
    onMoveDown,
    onRemove,
    onAdd,
  }: Props = $props();

  // The droppable data type is the column, the dndRules of the provider map it
  // to the accepted active type ("item")
  const dndData = $derived({ id: item, data: { type: "item" } });
  // svelte-ignore state_referenced_locally
  const { attributes, listeners, isDragging, setNodeRef: setDraggableNode } = useDraggable(dndData);
  // svelte-ignore state_referenced_locally
  const { isOver, setNodeRef: setDroppableNode } = useDroppable({ id: item, data: { type: column } });

  // The drop line is only meaningful in the selected column: a drop on an
  // unused row just removes the item from the order
  const indicator = $derived(column === "selected" && dropIndicator?.targetId === item ? dropIndicator.position : null);
</script>

<div
  use:setDraggableNode
  use:setDroppableNode
  data-dragging={isDragging.current}
  data-slot="filter-order-item"
  data-column={column}
  class="drag-placeholder relative rounded-md"
>
  <div
    class={cn(
      "bg-card flex items-center gap-1 rounded-md border py-0.5 pr-0.5 pl-1.5 text-sm",
      isOver.current && column === "unused" && "bg-accent",
    )}
  >
    {#if column === "selected"}
      <span class="text-muted-foreground w-5 shrink-0 text-right text-xs tabular-nums">#{index + 1}</span>
    {/if}
    <!-- Only the grip activates a drag; the row body must not start one, so a
         click on a row never moves the item by accident -->
    <div
      {...listeners.current}
      {...attributes.current}
      class="text-muted-foreground flex h-6 shrink-0 cursor-grab items-center px-1 active:cursor-grabbing"
      aria-label={`${t.Input.FilterOrderDrag()} ${label}`}
    >
      <GripVertical class="size-4" />
    </div>
    <span data-slot="filter-order-label" class="min-w-0 flex-1 truncate">{label}</span>
    {#if invalid}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <span role="img" class="inline-flex" aria-label={t.Input.FilterOrderInvalid()}>
              <TriangleAlert class="text-destructive size-3 shrink-0" />
            </span>
          </Tooltip.Trigger>
          <Tooltip.Content>
            <p>{t.Input.FilterOrderInvalid()}</p>
          </Tooltip.Content>
        </Tooltip.Root>
      </Tooltip.Provider>
    {/if}
    {#if column === "selected"}
      <!-- No gap between the row buttons, the row stays compact -->
      <div class="flex items-center">
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={index === 0}
          aria-label={`${t.Input.FilterOrderMoveUp()} ${label}`}
          onclick={onMoveUp}
        >
          <ArrowUp />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={index === count - 1}
          aria-label={`${t.Input.FilterOrderMoveDown()} ${label}`}
          onclick={onMoveDown}
        >
          <ArrowDown />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          class="text-destructive hover:text-destructive"
          aria-label={`${t.Input.FilterOrderRemove()} ${label}`}
          onclick={onRemove}
        >
          <Trash2 />
        </Button>
      </div>
    {:else}
      <Button variant="ghost" size="icon-xs" aria-label={`${t.Input.FilterOrderAdd()} ${label}`} onclick={onAdd}>
        <Plus />
      </Button>
    {/if}
  </div>

  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>
