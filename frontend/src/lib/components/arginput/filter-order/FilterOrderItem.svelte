<script lang="ts">
  import { type DropIndicatorState } from "$lib/components/dnd";
  import * as Select from "$lib/components/ui/select";
  import { t } from "$lib/i18n";
  import FilterItem from "./FilterItem.svelte";
  import FilterItemMarks from "./FilterItemMarks.svelte";

  /**
   * One row of the filter-order editor
   *
   * The row frame (element, drag registration, position, drop line, buttons) is
   * FilterItem.svelte; this component decides what the row shows: the label, the
   * secondary option edited in place, and the marks. The unused column adds the
   * row with a plus button, the selected column orders it.
   */
  type Props = {
    /** The dnd identity of the row: rows are addressed by index, a value may repeat */
    rowId: string;
    /** The item value, unique over both columns together */
    item: string;
    /** Which column the row lives in */
    column: "selected" | "unused";
    /** Label of the item, translated through option_i18n by the caller */
    label: string;
    /** 0-based position in the selected column, unused for the unused column */
    index?: number;
    /** Length of the selected column, for the down button of the last row */
    count?: number;
    /** True for an item that is not in the legal items anymore (an old value) */
    invalid?: boolean;
    /** True for the item of this row repeated from an earlier row */
    duplicate?: boolean;
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
    rowId,
    item,
    column,
    label,
    index = undefined,
    count = 0,
    invalid = false,
    duplicate = false,
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

  // Only a row of the order carries a position, the unused column adds rows
  const ordered = $derived(column === "selected");
</script>

<FilterItem
  {rowId}
  {item}
  index={ordered ? index : undefined}
  {count}
  {label}
  droppableType={column}
  rowSlot="filter-order-item"
  {dropIndicator}
  {onMoveUp}
  {onMoveDown}
  {onRemove}
  onAdd={ordered ? undefined : onAdd}
>
  <!-- The name and its secondary option share one box, the selector takes 2/5 of
       that box: the order buttons and the grip are not part of it, so the columns
       line up row by row -->
  <span data-slot="filter-order-label" class="min-w-0 flex-1 truncate">{label}</span>
  {#if ordered && secondaries.length > 0}
    <!-- The secondary option of the row, edited in place: the item of the value
         is "item-option" and the position never moves -->
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

  {#if duplicate || invalid}
    <FilterItemMarks {duplicate} {invalid} />
  {/if}
</FilterItem>
