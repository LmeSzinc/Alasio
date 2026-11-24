<script lang="ts">
  import { useDraggable, useDroppable } from "@dnd-kit-svelte/core";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import Trash2 from "@lucide/svelte/icons/trash-2";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { type DropIndicatorState, Indicator } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import * as Select from "$lib/components/ui/select";
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
    /** True for an item that is not in the legal items anymore (an old value) */
    invalid?: boolean;
    /** Secondary option of the row, null when its primary has none */
    secondary?: string | null;
    /** Secondary options the primary declares, empty to render no selector */
    secondaries?: string[];
    /** True when the secondary option of the row is not declared anymore */
    secondaryInvalid?: boolean;
    /** Label of a secondary option, translated through option_i18n by the caller */
    secondaryLabel?: (value: string) => string;
    /** Called with the new secondary option of the row */
    onSecondaryChange?: (next: string) => void;
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
    secondary = null,
    secondaries = [],
    secondaryInvalid = false,
    secondaryLabel = (value: string) => value,
    onSecondaryChange,
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
      "flex items-center gap-1 rounded-md border bg-card py-0.5 pr-0.5 pl-1.5 text-sm",
      isOver.current && column === "unused" && "bg-accent",
    )}
  >
    {#if column === "selected"}
      <span class="w-5 shrink-0 text-right text-xs text-muted-foreground tabular-nums">#{index + 1}</span>
    {/if}
    <!-- Only the grip activates a drag; the row body must not start one, so a
         click on a row never moves the item by accident -->
    <div
      {...listeners.current}
      {...attributes.current}
      class="flex h-6 shrink-0 cursor-grab items-center px-1 text-muted-foreground"
      aria-label={`${t.Input.FilterOrderDrag()} ${label}`}
    >
      <GripVertical class="size-4" />
    </div>
    <!-- The name and its secondary option share one box, the selector takes
         2/5 of that box: the order buttons and the grip are not part of it, so
         the columns line up row by row -->
    <div class="flex min-w-0 flex-1 items-center gap-1">
      <span data-slot="filter-order-label" class="min-w-0 flex-1 truncate">{label}</span>
      {#if column === "selected" && secondaries.length > 0}
        <!-- The secondary option of the row, edited in place: the item of the
             value is "item-option" and the position never moves -->
        {#if secondaries.length === 1 && !secondaryInvalid}
          <span class="w-2/5 shrink-0 truncate text-xs text-muted-foreground">
            {secondaryLabel(secondaries[0])}
          </span>
        {:else}
          <Select.Root
            type="single"
            value={secondary ?? undefined}
            onValueChange={(value) => value && onSecondaryChange?.(value)}
          >
            <Select.Trigger
              size="sm"
              class="h-6! w-2/5 shrink-0 gap-1 border-0 bg-accent px-1.5 py-0 text-xs shadow-none"
              aria-label={`${t.Input.FilterOrderSecondary()} ${label}`}
            >
              <span class="min-w-0 flex-1 truncate text-left" class:text-destructive={secondaryInvalid}>
                {secondary === null ? "" : secondaryLabel(secondary)}
              </span>
            </Select.Trigger>
            <Select.Content>
              <Select.Group>
                {#if secondaryInvalid && secondary !== null}
                  <Select.Item value={secondary} label={secondaryLabel(secondary)}>
                    <span class="text-destructive">{secondaryLabel(secondary)}</span>
                  </Select.Item>
                {/if}
                {#each secondaries as option (option)}
                  {@const optionLabel = secondaryLabel(option)}
                  <Select.Item value={option} label={optionLabel}>{optionLabel}</Select.Item>
                {/each}
              </Select.Group>
            </Select.Content>
          </Select.Root>
        {/if}
      {/if}
    </div>
    {#if invalid}
      <Tooltip.Provider>
        <Tooltip.Root>
          <Tooltip.Trigger>
            <span role="img" class="inline-flex" aria-label={t.Input.FilterOrderInvalid()}>
              <TriangleAlert class="size-3 shrink-0 text-destructive" />
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
        <!-- Up / down are the two halves of one diamond: two triangles that
             share their waist, so the control reads as a single rotated square
             while each half stays its own button (each one highlights and
             disables on its own). The shape is the SVG fill, the icon keeps the
             size and the gap of the outlined variant -->
        <div class="flex shrink-0 flex-col gap-px">
          <button
            type="button"
            class="flex h-2 w-4 items-center justify-center text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground disabled:pointer-events-none disabled:text-muted-foreground/25"
            disabled={index === 0}
            aria-label={`${t.Input.FilterOrderMoveUp()} ${label}`}
            onclick={onMoveUp}
          >
            <!-- Filled triangle: the path is closed and painted with the
                 current text color, so hover / disabled follow the row -->
            <svg viewBox="0 0 12 6" class="h-1.5 w-3" aria-hidden="true">
              <path d="M6 1 11 5H1z" fill="currentColor" stroke="none" />
            </svg>
          </button>
          <button
            type="button"
            class="flex h-2 w-4 items-center justify-center text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground disabled:pointer-events-none disabled:text-muted-foreground/25"
            disabled={index === count - 1}
            aria-label={`${t.Input.FilterOrderMoveDown()} ${label}`}
            onclick={onMoveDown}
          >
            <svg viewBox="0 0 12 6" class="h-1.5 w-3" aria-hidden="true">
              <path d="M1 1h10L6 5z" fill="currentColor" stroke="none" />
            </svg>
          </button>
        </div>
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
