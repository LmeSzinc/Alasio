<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import type { ArgData } from "../arg/utils.svelte";
  import DashboardItem from "./DashboardItem.svelte";

  let {
    items,
    class: className,
  }: {
    items: Record<string, Record<string, ArgData>>;
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
  row gap, gap-x-2 = 0.5rem). So a row always holds at least two columns: a too
  narrow dashboard compresses the items instead of dropping down to one column.
  Tracks are `1fr` (they share the row equally), and `auto-fill` keeps the
  empty tracks, so a last row that is not full keeps the column width of the
  rows above, with the trailing slots left empty.

  Rows: `max-content`, never the default `auto`. An `auto` row is sized from the
  items' automatic minimum size, which is 0 here because an item clips its own
  overflow, and a card whose height is smaller than its content then shrinks the
  rows to fit instead of overflowing: the items get squashed and lose their
  second line. With `max-content` a row is always as tall as its item content,
  and a card that is too short overflows into the scroll port as expected.
-->
<ScrollArea class={className}>
  <div
    class="grid auto-rows-max grid-cols-[repeat(auto-fill,minmax(min(8.75rem,calc(50%_-_0.25rem)),1fr))] content-start gap-x-2 gap-y-3 p-4"
  >
    {#each itemList as [itemKey, data] (itemKey)}
      <DashboardItem {data} />
    {/each}
  </div>
</ScrollArea>
