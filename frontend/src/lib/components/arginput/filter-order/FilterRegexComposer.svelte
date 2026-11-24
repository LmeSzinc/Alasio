<script lang="ts">
  import { useDraggable } from "@dnd-kit-svelte/core";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import Plus from "@lucide/svelte/icons/plus";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import FilterRegexLevels from "./FilterRegexLevels.svelte";
  import { type RegexLevel, type RegexValues, encodeRegex } from "./filterOrder";

  /**
   * The always visible constructor row of the dt="filter-regex" editor.
   *
   * It is the last row of the list: fill the levels, then press the plus button
   * to append the item, or drag the row into the list to insert it right there.
   * The commands are the small buttons on its right, each one appends its own
   * row. The row itself is not part of the value, so it is never validated.
   */
  type Props = {
    /** Levels of the arg, in the declared order */
    levels: RegexLevel[];
    /** Commands of the arg, each one is a button that appends a command row */
    commands: string[];
    /** Label of a level value, translated through option_i18n by the caller */
    label: (value: string) => string;
    /** Called with the encoded item the row currently describes */
    onAdd: (item: string) => void;
  };
  let { levels, commands, label, onAdd }: Props = $props();

  // The selection of the constructor row, emptied after the row is added
  let values = $state<RegexValues>({});
  const item = $derived(encodeRegex(levels, values));

  // The constructor row is draggable: its data carries the item it describes
  // at the moment the drag starts, and no index, so a drop inserts it
  // svelte-ignore state_referenced_locally
  const { attributes, listeners, isDragging, setNodeRef } = useDraggable({
    id: "filter-regex-composer",
    data: { type: "item", item },
  });

  function onLevelChange(name: string, value: string) {
    values = { ...values, [name]: value };
  }

  function add() {
    if (!item) return;
    onAdd(item);
    values = {};
  }
</script>

<div
  use:setNodeRef
  data-dragging={isDragging.current}
  data-slot="filter-regex-composer"
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
    {#each commands as command (command)}
      <Button variant="outline" size="xs" class="h-6 shrink-0 px-1.5 text-xs" onclick={() => onAdd(command)}>
        {command}
      </Button>
    {/each}
    <Button variant="ghost" size="icon-xs" disabled={!item} aria-label={t.Input.FilterOrderAdd()} onclick={add}>
      <Plus />
    </Button>
  </div>
</div>
