<script lang="ts">
  import { useDraggable } from "@dnd-kit-svelte/core";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import FilterCommandItem from "./FilterCommandItem.svelte";
  import FilterRegexLevels from "./FilterRegexLevels.svelte";
  import { type RegexLevel, type RegexValues, encodeRegex } from "./filterOrder";

  /**
   * The always visible constructor area of the dt="filter-regex" editor.
   *
   * The first row builds a predicate: fill the levels, then press the plus button
   * to append the item, or drag the row into the list to insert it right there.
   * The rows below it are the commands the arg declares: every command is a chip
   * with its own grip and add button, so a command is added either by dragging it
   * where it belongs or by appending it. Neither the constructor row nor a chip
   * is part of the value, so neither is validated.
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

  const usedSet = $derived(new Set(used));

  function onLevelChange(name: string, value: string) {
    values = { ...values, [name]: value };
  }

  function add() {
    if (!item) return;
    onAdd(item);
    values = {};
  }
</script>

<div data-slot="filter-regex-composer">
  <div
    use:setNodeRef
    data-dragging={isDragging.current}
    data-slot="filter-regex-composer-row"
    class="drag-placeholder relative rounded-md"
  >
    <div class="flex items-center gap-1 rounded-md border border-dashed bg-card py-0.5 pr-0.5 pl-1.5 text-sm">
      <span class="w-5 shrink-0 text-right text-xs text-muted-foreground">+</span>
      <div
        {...listeners.current}
        {...attributes.current}
        class="flex h-6 shrink-0 cursor-grab items-center px-1 text-muted-foreground"
        aria-label={t.Input.FilterOrderDrag()}
      >
        <GripVertical class="size-4" />
      </div>
      <div class="flex min-w-0 flex-1 items-center gap-1">
        <FilterRegexLevels {levels} {values} {label} onchange={onLevelChange} />
      </div>
      <span class="max-w-40 shrink-0 truncate text-xs text-muted-foreground" data-slot="filter-regex-preview">
        {item}
      </span>
      <Button variant="ghost" size="icon-xs" disabled={!item} aria-label={t.Input.FilterOrderAdd()} onclick={add}>
        <Plus />
      </Button>
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
