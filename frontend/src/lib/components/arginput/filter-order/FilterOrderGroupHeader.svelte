<script lang="ts">
  /**
   * The header of one group of the unused column of a filter-order-pair.
   *
   * It is not draggable and not droppable on purpose: the header is a label of
   * the rows below it, not an entry of the value. A drop on it lands on the
   * column, which appends the row to the end of the order.
   */
  type Props = {
    /** Label of the primary item, already translated by the caller */
    label: string;
    /** How many pairs of this item are still unused, the rows below the header */
    remaining: number;
    /** How many pairs the item declares */
    total: number;
  };
  let { label, remaining, total }: Props = $props();
</script>

<div
  data-slot="filter-order-group"
  class="mt-1 flex shrink-0 items-center justify-between gap-2 rounded-sm bg-muted/70 px-1.5 py-0.5 text-xs font-medium text-muted-foreground select-none"
>
  <span class="min-w-0 truncate">{label}</span>
  <span class="shrink-0 tabular-nums" data-slot="filter-order-group-count">{remaining}/{total}</span>
</div>
