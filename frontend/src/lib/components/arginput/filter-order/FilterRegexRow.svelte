<script lang="ts">
  import { type DropIndicatorState } from "$lib/components/dnd";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import FilterItem from "./FilterItem.svelte";
  import FilterItemMarks from "./FilterItemMarks.svelte";
  import FilterRegexLevels from "./FilterRegexLevels.svelte";
  import type { RegexLevel, RegexValues } from "./filterOrder";

  /**
   * How many rows of the list are prepared with the dialog
   *
   * The first rows of a filter are certainly on screen when the dialog opens,
   * preparing them up front means the rows the user looks at first never show
   * as plain text. Everything below is prepared by `visibleOnce`, when it comes
   * to the screen.
   */
  const EAGER_ROWS = 12;

  /**
   * One row of the dt="filter-regex" editor
   *
   * The row frame (element, drag registration, position, drop line, buttons) is
   * FilterItem.svelte; this component decides what the middle of the row edits.
   *
   * A command carries no level, and an item the pattern can not read can not be
   * rendered as selects, both show a plain label instead.
   *
   * A row of a long filter mounts as the plain text of its value and turns into
   * its selects once it is on screen (see `visibleOnce`): the selects of one row
   * cost more than the row itself, and a filter of a hundred rows is opened far
   * more often than the rows of its end are read.
   */
  type Props = {
    /** The drag identity of the row, rows are addressed by index */
    rowId: string;
    /** The item value, as stored */
    item: string;
    /** 0-based position in the list */
    index: number;
    /** Length of the list, for the down button of the last row */
    count: number;
    /** Levels of the arg, in the declared order */
    levels: RegexLevel[];
    /** Level values of the item, null for a command or an undecodable item */
    values: RegexValues | null;
    /** The label of a command or of an undecodable item */
    text: string;
    /** True when the item is not decoded by the pattern */
    invalid?: boolean;
    /** True for the item of this row repeated from an earlier row */
    duplicate?: boolean;
    /** Label of a level value, translated through option_i18n by the caller */
    label: (value: string) => string;
    /** Called with the level name and its new value, empty for "any" */
    onLevelChange: (name: string, value: string) => void;
    dropIndicator?: DropIndicatorState | null;
    onMoveUp?: () => void;
    onMoveDown?: () => void;
    onRemove?: () => void;
  };
  let {
    rowId,
    item,
    index,
    count,
    levels,
    values,
    text,
    invalid = false,
    duplicate = false,
    label,
    onLevelChange,
    dropIndicator = null,
    onMoveUp,
    onMoveDown,
    onRemove,
  }: Props = $props();

  // The selects of the row are built once the row is on screen, an item that is
  // not a level predicate never gets them at all
  // svelte-ignore state_referenced_locally
  let hydrated = $state(index < EAGER_ROWS);
  const showLevels = $derived(values !== null && hydrated);
</script>

<FilterItem
  {rowId}
  {item}
  {index}
  {count}
  label={text}
  {dropIndicator}
  onVisible={() => (hydrated = true)}
  {onMoveUp}
  {onMoveDown}
  {onRemove}
>
  {#if showLevels && values !== null}
    <FilterRegexLevels {levels} {values} {label} onchange={onLevelChange} />
  {:else}
    <!-- The plain text of the value, the same the selects read once the row is
         prepared: the row keeps its height and never reflows -->
    <span
      data-slot="filter-regex-label"
      class={cn("flex h-6 min-w-0 flex-1 items-center truncate text-xs", invalid && "text-destructive")}
    >
      {text}
    </span>
  {/if}

  {#if duplicate || invalid}
    <FilterItemMarks {duplicate} {invalid} />
  {/if}
</FilterItem>
