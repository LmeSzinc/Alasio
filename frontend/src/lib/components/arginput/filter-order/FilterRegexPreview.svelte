<script lang="ts">
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";

  /**
   * The output preview of a dt="filter-regex" row: the item the row composes,
   * in the form it is stored.
   *
   * The selects of a row read the translated labels of the level values, the
   * preview reads the value itself, so the user sees what a save would write.
   * It heads the editor, right after the grip, and takes the width of one
   * level: the previews of the rows line up in a column of their own.
   *
   * An empty item is the row of the constructor before anything is picked: it
   * reads the placeholder of an unset value, the same the empty filter shows.
   */
  type Props = {
    /** The item of the row, as it would be stored, empty when nothing is set */
    item: string;
    /** True for an item the pattern can not read, it is shown as the error */
    invalid?: boolean;
  };
  let { item, invalid = false }: Props = $props();

  const empty = $derived(item === "");
  // The placeholder is set apart from a composed item by its parentheses: a
  // stored item never carries them, so the two can not be confused
  const text = $derived(empty ? `(${t.Input.FilterOrderEmpty()})` : item);
</script>

<span
  data-slot="filter-regex-preview"
  data-empty={empty}
  title={empty ? undefined : item}
  class={cn(
    "flex h-6 min-w-0 flex-1 items-center text-xs",
    empty && "text-muted-foreground",
    invalid && "text-destructive",
  )}
>
  <!-- The text sits in an element of its own: the truncation of a flex container
       does not apply to its anonymous text child -->
  <span class="min-w-0 flex-1 truncate">{text}</span>
</span>
