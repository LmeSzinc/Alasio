<script lang="ts">
  import { useDraggable } from "@dnd-kit-svelte/core";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import FilterCommandItem from "./FilterCommandItem.svelte";
  import FilterRegexLevels from "./FilterRegexLevels.svelte";
  import FilterRegexPreview from "./FilterRegexPreview.svelte";
  import { type RegexLevel, type RegexValues, encodeRegex } from "./filterOrder";

  /**
   * The always visible constructor area of the dt="filter-regex" editor.
   *
   * The first row builds a predicate: fill the levels, then press the plus button
   * to append the item, or drag the row into the list to insert it right there.
   * Its preview heads the row, right after the grip, and reads the string the
   * levels compose, so the row shows what a save would write next to the labels
   * of the levels. The rows below it are the commands the arg declares: every
   * command is a chip with its own grip and add button, so a command is added
   * either by dragging it where it belongs or by appending it. Neither the
   * constructor row nor a chip is part of the value, so neither is validated.
   *
   * The constructor row is not in the scroll port, but it sits on the grid of
   * the rows inside it: the same horizontal inset as the scrolled content, and
   * the two buttons of a row (the reset of the selection, then the add), so the
   * preview and the level selects of every row share one column each.
   */
  type Props = {
    /** Levels of the arg, in the declared order */
    levels: RegexLevel[];
    /** Commands the arg declares, in the declared order */
    commands: string[];
    /** Commands the order already holds, they are shown as used */
    used?: string[];
    /** Label of a level value, translated through option_i18n by the caller */
    label: (value: string) => string;
    /** Called with the encoded item the constructor row describes, or a command */
    onAdd: (item: string) => void;
  };
  let { levels, commands, used = [], label, onAdd }: Props = $props();

  // The selection of the constructor row, emptied after the row is added
  let values = $state<RegexValues>({});
  const item = $derived(encodeRegex(levels, values));

  // The constructor row is draggable: its data carries the item it describes
  // at the moment the drag starts, and no index, so a drop inserts it.
  //
  // The drag registration keeps the data object it is given, it does not copy
  // it, so the object has to outlive the registration and follow the levels: a
  // fresh object literal would freeze the item to the empty string the row had
  // when it mounted, and the drop would insert nothing.
  const draft = $state({ type: "item", item: "" });
  $effect(() => {
    draft.item = item;
  });
  const { attributes, listeners, isDragging, setNodeRef } = useDraggable({
    id: "filter-regex-composer",
    data: draft,
  });

  // The grip lifts the row only while it describes an item: an empty row drags
  // nothing, and a drop of it inserts nothing, so it must not start a drag. The
  // listeners are dropped together with the cursor, a drag that begins on the
  // grip is the only way this row leaves the list.
  const gripListeners = $derived(item ? listeners.current : {});
  const canDrag = $derived(item !== "");

  const usedSet = $derived(new Set(used));

  // A disabled button must look dead on hover, both in paint and in the cursor.
  // The ghost variant only draws a hover state, and the base class drops the
  // pointer events of a disabled button, which also drops its cursor: the
  // element keeps `cursor: default` and the control reads as one that does
  // nothing. The look is pinned here instead: the pointer events come back so
  // the cursor is taken from this button, no background is painted, and the
  // text keeps the colour of the row
  const DEAD_HOVER =
    "disabled:pointer-events-auto disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-current";

  function onLevelChange(name: string, value: string) {
    values = { ...values, [name]: value };
  }

  function add() {
    if (!item) return;
    onAdd(item);
    values = {};
  }

  /** Drop the selection, the row returns to "any" on every level */
  function reset() {
    values = {};
  }
</script>

<!-- The inset is the one of the scrolled rows (see FilterOrderColumn): the
     border of this row and the borders of the rows above end on the same edge -->
<div data-slot="filter-regex-composer" class="p-0.5 pr-2.5">
  <div
    use:setNodeRef
    data-dragging={isDragging.current}
    data-slot="filter-regex-composer-row"
    class="drag-placeholder relative rounded-md"
  >
    <div class="flex items-center gap-1 rounded-md border border-dashed bg-card py-0.5 pr-0.5 pl-1.5 text-sm">
      <span class="w-5 shrink-0 text-right text-xs text-muted-foreground">+</span>
      <div
        {...attributes.current}
        {...gripListeners}
        role="button"
        tabindex={canDrag ? 0 : -1}
        aria-disabled={!canDrag}
        class={cn(
          "flex h-6 shrink-0 items-center px-1 text-muted-foreground",
          canDrag ? "cursor-grab" : "cursor-not-allowed opacity-50",
        )}
        aria-label={t.Input.FilterOrderDrag()}
      >
        <GripVertical class="size-4" />
      </div>
      <div class="flex min-w-0 flex-1 items-center gap-1">
        <FilterRegexPreview {item} />
        <FilterRegexLevels {levels} {values} {label} onchange={onLevelChange} />
      </div>
      <!-- Two buttons, the same as a row of the list, on the same grid: the
           reset is as wide as the up / down diamond of a row above, the add
           sits in the column of its remove button. Both are disabled while the
           row describes nothing -->
      <div class="flex items-center">
        <Button
          variant="ghost"
          size="icon-xs"
          class={cn("w-4", DEAD_HOVER)}
          disabled={!item}
          aria-label={t.Input.FilterOrderClear()}
          onclick={reset}
        >
          <RotateCcw />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          class={DEAD_HOVER}
          disabled={!item}
          aria-label={t.Input.FilterOrderAdd()}
          onclick={add}
        >
          <Plus />
        </Button>
      </div>
    </div>
  </div>

  {#if commands.length > 0}
    <!-- The commands wrap, and stop at three lines: a filter may declare a dozen
         of them, and the add area must not push the list out of the dialog. The
         box scrolls instead, so every command stays reachable -->
    <div
      data-slot="filter-commands"
      class="mt-1.5 flex max-h-20 flex-wrap content-start items-center gap-1 overflow-y-auto"
    >
      {#each commands as command (command)}
        <FilterCommandItem {command} label={label(command)} used={usedSet.has(command)} onAdd={() => onAdd(command)} />
      {/each}
    </div>
  {/if}
</div>
