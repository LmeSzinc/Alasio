<script lang="ts">
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import type { Snippet } from "svelte";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import { type DropIndicatorState, Indicator } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { visibleOnce } from "$lib/use/visible.svelte";
  import { cn } from "$lib/utils";
  import FilterRowActions from "./FilterRowActions.svelte";

  /**
   * One row of an ordered filter editor, of either column of the transfer dialog
   * or of the single column of dt="filter-regex"
   *
   * The row owns its element and everything the list needs from it: it registers
   * itself as a draggable item, registers as a droppable target of its own
   * column, draws the drop line the list state points at it, mounts itself as
   * plain text until it is on screen, and renders the position and the buttons.
   * No child is ever asked for its node, so the node flows down from here.
   *
   * What varies per column is a prop, not a copy of the component:
   * - a row with an `index` is a row of the order: it shows the position and the
   *   up / down / remove buttons;
   * - a row with `onAdd` is a row of the unused column: no position, a plus
   *   button, and a drop on it removes the item from the order, so the drop line
   *   is not drawn for it (`droppableType` differs, a drop on it is the signal).
   */
  type Props = {
    /** The drag identity of the row, the list state addresses rows by it */
    rowId: string;
    /** The value the row carries, handed to the drag */
    item: string;
    /** 0-based position in the order, absent for a row of the unused column */
    index?: number;
    /** Length of the order, for the down button of the last row */
    count?: number;
    /** Text of the row in the accessible labels, e.g. "Move up {label}" */
    label: string;
    /** Type of the droppable the row registers as, the dnd rules map it */
    droppableType?: "selected" | "unused";
    /** The data-slot of the row element, so a view can find the rows of a list */
    rowSlot?: string;
    /** Drop line of the whole list, the row draws it only when it targets this row */
    dropIndicator?: DropIndicatorState | null;
    /** Called once when the row first comes to the screen, for a lazy editor */
    onVisible?: () => void;
    onMoveUp?: () => void;
    onMoveDown?: () => void;
    onRemove?: () => void;
    /** Adds the row to the order, it replaces the order buttons of the row */
    onAdd?: () => void;
    /** The content of the row: the editor, and whatever marks it carries */
    children: Snippet;
  };
  let {
    rowId,
    item,
    index = undefined,
    count = 0,
    label,
    droppableType = "selected",
    rowSlot = "filter-item",
    dropIndicator = null,
    onVisible,
    onMoveUp,
    onMoveDown,
    onRemove,
    onAdd,
    children,
  }: Props = $props();

  const ordered = $derived(index !== undefined);
  // The position and the move bounds of a row of the order, a row of the unused
  // column has neither
  const at = $derived(index ?? 0);

  // The id is the row, never the value: a stored value may repeat an item, and
  // two rows sharing an id would be dragged and highlighted together. The data
  // carries the index so the drop math can address the row.
  // svelte-ignore state_referenced_locally
  const {
    attributes,
    listeners,
    isDragging,
    setNodeRef: setDraggableNode,
  } = useDraggable({
    id: rowId,
    data: { type: "item", item, index },
  });
  // svelte-ignore state_referenced_locally
  const { isOver, setNodeRef: setDroppableNode } = useDroppable({
    id: rowId,
    data: { type: droppableType, index },
  });

  // The row is both a draggable item and a drop target of its column
  function setRowNode(node: HTMLElement) {
    setDraggableNode(node);
    setDroppableNode(node);
  }

  /**
   * The drop line of this row. Only a row of the order draws one: a drop on a row
   * of the unused column means "take it out of the order", there is no position
   * to point at.
   */
  const indicator = $derived.by((): "top" | "bottom" | null => {
    if (!ordered || dropIndicator?.targetId !== rowId) return null;
    // The list is vertical, a horizontal edge is not drawn
    const position = dropIndicator.position;
    return position === "top" || position === "bottom" ? position : null;
  });

  // A row of the unused column paints itself while it is hovered by a drag
  const hovered = $derived(!ordered && isOver.current);
</script>

<div
  use:setRowNode
  use:visibleOnce={() => onVisible?.()}
  data-dragging={isDragging.current}
  data-slot={rowSlot}
  class="drag-placeholder relative rounded-md"
>
  <div
    class={cn("flex items-center gap-1 rounded-md border bg-card py-0.5 pr-0.5 pl-1.5 text-sm", hovered && "bg-accent")}
  >
    {#if ordered}
      <!-- The position carries the theme colour and a bold weight: it reads as
           a counter of the order, not as a piece of the row's content -->
      <span class="w-5 shrink-0 text-right text-xs font-bold text-primary tabular-nums">#{at + 1}</span>
    {/if}
    <!-- Only the grip activates a drag; the row body must not start one, so a
         click in the editor of the row never moves the item by accident -->
    <div
      {...listeners.current}
      {...attributes.current}
      class="flex h-6 shrink-0 cursor-grab items-center px-1 text-muted-foreground"
      aria-label={`${t.Input.FilterOrderDrag()} ${label}`}
    >
      <GripVertical class="size-4" />
    </div>
    <div class="flex min-w-0 flex-1 items-center gap-1">
      {@render children()}
    </div>
    {#if onAdd}
      <Button variant="ghost" size="icon-xs" aria-label={`${t.Input.FilterOrderAdd()} ${label}`} onclick={onAdd}>
        <Plus />
      </Button>
    {:else if ordered}
      <FilterRowActions {label} canMoveUp={at > 0} canMoveDown={at < count - 1} {onMoveUp} {onMoveDown} {onRemove} />
    {/if}
  </div>

  {#if indicator === "top"}
    <Indicator edge="top" />
  {:else if indicator === "bottom"}
    <Indicator edge="bottom" />
  {/if}
</div>
