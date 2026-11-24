<script lang="ts">
  import { type ArgData, getArgName, i18nTextToOneLine, useArgValue } from "$lib/components/arg/utils.svelte";
  import { type DndEndCallbackDetail } from "$lib/components/dnd";
  import { t } from "$lib/i18n";
  import FilterOrderColumn from "./FilterOrderColumn.svelte";
  import FilterPopup from "./FilterPopup.svelte";
  import FilterRegexComposer from "./FilterRegexComposer.svelte";
  import FilterRegexRow from "./FilterRegexRow.svelte";
  import {
    type DropTarget,
    type FilterOrderItem as FilterOrderItemValue,
    type RegexLevel,
    type RegexValues,
    decodeRegex,
    duplicateIndexes,
    insertRow,
    moveItem,
    moveRow,
    removeAt,
    sameList,
    setRegexLevel,
  } from "./filterOrder";

  /**
   * The single column editor of dt="filter-regex".
   *
   * The candidates of a multi level filter are a cartesian product (thousands
   * of items for a research filter), so there is no "unused" column: the user
   * describes a predicate instead of picking an item, and every row is its own
   * editor. The list is one column, and the row that adds a new predicate sits
   * below it under its own heading, so there is no "add" mode and no "current
   * row" state to explain.
   *
   * Every edit only touches the local `draft`; the value is committed through
   * `onSave` when the user presses save with a changed list. The dialog shell,
   * the footer buttons and the "edits discarded" toast of a cancel live in
   * FilterPopup.svelte, shared with the other filter editors.
   */
  type Props = {
    open?: boolean;
    data: ArgData;
    value: FilterOrderItemValue[];
    onSave: (next: FilterOrderItemValue[]) => void;
    onReset?: () => void;
  };
  let { open = $bindable(false), data, value, onSave, onReset }: Props = $props();

  const arg = $derived(useArgValue<FilterOrderItemValue[]>(data));
  // Dialog title is a heading of one row, so a name of multiple lines is flattened
  const displayName = $derived(i18nTextToOneLine(getArgName(data)));

  // Working copy, only save commits it
  let draft = $state<FilterOrderItemValue[]>([]);
  let wasOpen = $state(false);
  $effect.pre(() => {
    // Initialize the draft when the dialog opens; an external change of
    // `value` while it is open must not overwrite the pending edits
    if (open && !wasOpen) draft = [...value];
    wasOpen = open;
  });

  /**
   * True once the dialog is on screen, the rows are mounted after that
   *
   * A filter value holds dozens of rows and every row is an editor: mounting
   * them all before the dialog is painted makes the click on the edit button
   * wait for the whole list. The shell of the dialog is painted first, the rows
   * join it on the next frame, so the dialog opens at once and the list fills
   * in a frame later.
   */
  let rowsReady = $state(false);
  $effect(() => {
    if (!open) {
      rowsReady = false;
      return;
    }
    const frame = requestAnimationFrame(() => (rowsReady = true));
    return () => cancelAnimationFrame(frame);
  });

  const levels = $derived<RegexLevel[]>(Array.isArray(data.levels) ? data.levels : []);
  const pattern = $derived(data.pattern ?? null);
  const optionCommand = $derived(Array.isArray(data.option_command) ? data.option_command.map(String) : []);
  const commandSet = $derived(new Set(optionCommand));

  /**
   * The level values of every row, decoded once per change
   *
   * Every row asks for its values several times per render (the selects, the
   * label of the aria-labels, the invalid mark), and decoding means running the
   * pattern of the arg: a filter value holds a hundred rows, so the decode runs
   * once here and the rows and the marks share the result.
   */
  const decoded = $derived.by(() => {
    const map = new Map<string, RegexValues | null>();
    for (const item of draft) {
      const text = String(item);
      map.set(text, commandSet.has(text) ? null : decodeRegex(pattern, text));
    }
    return map;
  });

  /** The level values of a row, null for a command or an item the pattern can not read */
  function valuesOf(item: FilterOrderItemValue): RegexValues | null {
    return decoded.get(String(item)) ?? null;
  }

  /** True when the row is not readable nor a command: only removing it helps */
  function isInvalid(item: FilterOrderItemValue): boolean {
    return valuesOf(item) === null && !commandSet.has(String(item));
  }

  /**
   * Label of a whole item: the translated level values joined by the level
   * separator, the same form the value is stored in. A command is shown as it
   * is stored, an unreadable item is shown as its raw text.
   */
  function labelOf(item: FilterOrderItemValue): string {
    const text = String(item);
    if (commandSet.has(text)) return text;
    const values = valuesOf(item);
    if (!values) return text;
    return levels
      .map((level) => values[level.name] ?? "")
      .filter((levelValue) => levelValue !== "")
      .map((levelValue) => arg.getLabel(levelValue))
      .join("-");
  }

  /** The text of a row in an aria-label, and of the drag overlay */
  function textOf(item: FilterOrderItemValue): string {
    return labelOf(item) || String(item);
  }

  /**
   * A stored value may repeat an item. Only the later occurrences are marked:
   * the first one is where the item belongs. The backend rejects the value on
   * save, so the editor blocks saving as it does for an unreadable item.
   */
  const duplicated = $derived(duplicateIndexes(draft.map(String)));
  const hasDuplicate = $derived(duplicated.size > 0);
  const hasInvalid = $derived(draft.some((item) => isInvalid(item)));
  const blocked = $derived(hasInvalid || hasDuplicate);

  const isDirty = $derived(!sameList(draft, value));

  const dndRules = { selected: ["item"] };

  function onMoveUp(index: number) {
    draft = moveItem(draft, index, -1);
  }

  function onMoveDown(index: number) {
    draft = moveItem(draft, index, 1);
  }

  function onRemove(index: number) {
    draft = removeAt(draft, index);
  }

  /** Replace one level of a row, the position of the row is kept */
  function onLevelChange(index: number, name: string, next: string) {
    const item = draft[index];
    if (item === undefined) return;
    const nextItem = setRegexLevel(levels, pattern, String(item), name, next);
    if (nextItem === null || nextItem === item) return;
    draft = [...draft.slice(0, index), nextItem, ...draft.slice(index + 1)];
  }

  function onAdd(item: FilterOrderItemValue) {
    if (!item) return;
    draft = [...draft, item];
  }

  /**
   * A drop addresses rows by index, never by value: a stored value may repeat
   * an item, and dragging one of two equal rows has to move only that row. The
   * constructor row carries no index, its drop inserts the item it describes.
   */
  function onDndEnd({ active, over, position }: DndEndCallbackDetail) {
    if (!active || !over) return;
    const activeData = active.data as { item?: string; index?: number } | undefined;
    const overData = over.data as { type?: string; container?: boolean; index?: number } | undefined;
    const at = position === "top" ? "top" : "bottom";
    if (overData?.type !== "selected") return;

    const target: DropTarget = overData.container
      ? { kind: "selected", position: at }
      : { kind: "selected", index: overData.index, position: at };
    const source = activeData?.index;
    if (source !== undefined) {
      draft = moveRow(draft, source, target);
      return;
    }
    const item = String(activeData?.item ?? "");
    if (!item) return;
    draft = insertRow(draft, item, target);
  }
</script>

<FilterPopup
  bind:open
  title={displayName}
  dirty={isDirty}
  {blocked}
  {dndRules}
  {onDndEnd}
  onSave={() => onSave([...draft])}
  {onReset}
  overlayLabel={(item) => textOf(item)}
>
  {#snippet children({ dropIndicator })}
    <!-- One column, no heading: the list is the whole editor, and a heading of
         "selected" would suggest there is another column to compare with -->
    <div data-slot="filter-regex-column" class="flex min-h-0 min-w-0 flex-1 flex-col gap-y-1.5">
      <FilterOrderColumn column="selected">
        {#if draft.length === 0}
          <div class="flex min-h-16 items-center justify-center px-2 text-center text-xs text-muted-foreground">
            {t.Input.FilterOrderSelectedEmpty()}
          </div>
        {:else if !rowsReady}
          <!-- The list is filled on the frame after the dialog opens, so the
               click that opens it is not spent on the whole list -->
        {:else}
          <!-- The key carries the index: a repeated item is a legal row to
               show (the value is blocked until one of its rows is dropped),
               but two equal items can not share a key -->
          {#each draft as item, index (`${index}\u0000${item}`)}
            <FilterRegexRow
              rowId={String(index)}
              {item}
              {index}
              count={draft.length}
              {levels}
              values={valuesOf(item)}
              text={textOf(item)}
              invalid={isInvalid(item)}
              duplicate={duplicated.has(index)}
              label={(value) => arg.getLabel(value)}
              onLevelChange={(name, value) => onLevelChange(index, name, value)}
              onMoveUp={() => onMoveUp(index)}
              onMoveDown={() => onMoveDown(index)}
              onRemove={() => onRemove(index)}
              {dropIndicator}
            />
          {/each}
        {/if}
      </FilterOrderColumn>
      <!-- The row that adds a predicate sits below the scroll port, not in it:
           it is the one row the user always needs, scrolling to the end of a
           long list to reach it would make adding an item needlessly hard. It
           keeps its own heading, so it does not read as the last row of the
           list. It is still a draggable row, the drop is handled by the rows of
           the column as usual -->
      <div class="shrink-0" data-slot="filter-regex-add">
        <div class="mb-1.5 border-b border-border pb-1.5 text-xs font-medium text-muted-foreground">
          {t.Input.FilterOrderAddFilter()}
        </div>
        <FilterRegexComposer
          {levels}
          commands={optionCommand.filter((command) => !draft.includes(command))}
          label={(value) => arg.getLabel(value)}
          {onAdd}
        />
      </div>
    </div>
  {/snippet}
</FilterPopup>
