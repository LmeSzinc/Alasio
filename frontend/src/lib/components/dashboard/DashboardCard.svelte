<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import type { ArgData } from "../arg/utils.svelte";
  import DashboardItem from "./DashboardItem.svelte";

  let {
    items,
    overrideFlash,
    class: className,
  }: {
    items: Record<string, Record<string, ArgData>>;
    /**
     * Pin the flash state of every item instead of letting it react to the
     * values: `true` keeps them highlighted, `false` keeps them plain
     * (undefined leaves each item flashing on its own value changes). The debug
     * page pins a whole card to show the highlight without an update.
     */
    overrideFlash?: boolean;
    class?: string;
  } = $props();

  const itemList = $derived(Object.entries(items ?? {}));
</script>

<!--
  The dashboard data is flat: `{item_name: {arg_name: ArgData}}`, one item per
  dashboard group. So every item is displayed directly in one flow: there is no
  group to expand and no collapsed height to hide items behind, and a card that
  is too short scrolls instead.

  The flow scrolls in a `ScrollArea`, which owns the scroll port (its viewport)
  and draws its scrollbar on top of the content. A scrollbar that takes part in
  the layout (plain `overflow-y-auto` on the grid itself) eats ~15px of the grid
  width on Windows, and that is the difference between the widest item fitting
  and losing its time tail.

  Columns: `auto-fill` fits as many 8.75rem tracks into a row as the width
  allows, and the track minimum is capped at half of the row (minus half of the
  row gap, gap-x-1.5 = 0.375rem). So a row always holds at least two columns: a
  too narrow dashboard compresses the items instead of dropping down to one
  column. Tracks are `1fr` (they share the row equally), and `auto-fill` keeps
  the empty tracks, so a last row that is not full keeps the column width of
  the rows above, with the trailing slots left empty.

  The track minimum stays at the width the tracks had before the items began
  carrying their own padding (see below): padding an item must cost the item,
  never a column, so a given dashboard width keeps the column count it had.

  The gaps and the padding of the card are small because an item carries a good
  part of the spacing itself: its highlight is a block of its own, and the
  padding inside it (DashboardItem) is what keeps the text away from the
  highlight edge. So the flow spends its space on the highlight rather than
  between the highlights.

  Rows: `max-content`, never the default `auto`. An `auto` row is sized from the
  items' automatic minimum size, which is 0 here because an item clips its own
  overflow, and a card whose height is smaller than its content then shrinks the
  rows to fit instead of overflowing: the items get squashed and lose their
  second line. With `max-content` a row is always as tall as its item content,
  and a card that is too short overflows into the scroll port as expected.
-->
<ScrollArea class={className}>
  <div
    class="grid auto-rows-max grid-cols-[repeat(auto-fill,minmax(min(8.75rem,calc(50%-0.25rem)),1fr))] content-start gap-x-0.5 gap-y-0.5 p-3"
  >
    {#each itemList as [itemKey, data] (itemKey)}
      <DashboardItem {data} {overrideFlash} />
    {/each}
  </div>
</ScrollArea>
