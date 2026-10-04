<script lang="ts">
  import { toast } from "svelte-sonner";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import { type ArgData, getArgName, useArgValue } from "$lib/components/arg/utils.svelte";
  import { type DndEndCallbackDetail, DndProvider } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import FilterOrderColumn from "./FilterOrderColumn.svelte";
  import FilterOrderItem from "./FilterOrderItem.svelte";
  import {
    type DropTarget,
    type FilterOrderItem as FilterOrderItemValue,
    appendItem,
    applyDrop,
    moveItem,
    removeItem,
    sameList,
  } from "./filterOrder";

  /**
   * The transfer-like editor of dt="filter-order" (see
   * doc/2026-10-03_filter-order.md §3.2).
   *
   * Every edit only touches the local `draft`; the value is committed through
   * `onSave` when the user presses save with a changed order, and discarded
   * with an "edits discarded" toast when the dialog is cancelled.
   */
  type Props = {
    open?: boolean;
    data: ArgData;
    value: FilterOrderItemValue[];
    onSave: (next: FilterOrderItemValue[]) => void;
  };
  let { open = $bindable(false), data, value, onSave }: Props = $props();

  const arg = $derived(useArgValue<FilterOrderItemValue[]>(data));
  const displayName = $derived(getArgName(data));

  // Working copy, only save commits it
  let draft = $state<FilterOrderItemValue[]>([]);
  let wasOpen = $state(false);
  $effect.pre(() => {
    // Initialize the draft when the dialog opens; an external change of
    // `value` while it is open must not overwrite the pending edits
    if (open && !wasOpen) draft = [...value];
    wasOpen = open;
  });

  // The unused column is the rest of "option", it always follows the option
  // order, so a drag inside it has no visual effect
  const options = $derived(Array.isArray(data.option) ? data.option : []);
  const unused = $derived(options.filter((item) => !draft.includes(item)));

  /** True for an item that is in the order but not in "option" anymore */
  function isInvalid(item: FilterOrderItemValue): boolean {
    return options.length > 0 && !options.includes(item);
  }

  const isDirty = $derived(!sameList(draft, value));

  const dndRules = { selected: ["item"], unused: ["item"] };

  const toastOptions = {
    duration: 2000,
    classes: {
      // Skip header height
      toast: "mt-10",
    },
  };

  function labelOf(item: FilterOrderItemValue): string {
    return arg.getLabel(item);
  }

  function onMoveUp(item: FilterOrderItemValue) {
    draft = moveItem(draft, draft.indexOf(item), -1);
  }

  function onMoveDown(item: FilterOrderItemValue) {
    draft = moveItem(draft, draft.indexOf(item), 1);
  }

  function onRemove(item: FilterOrderItemValue) {
    draft = removeItem(draft, item);
  }

  function onAdd(item: FilterOrderItemValue) {
    draft = appendItem(draft, item);
  }

  function onDndEnd({ active, over, position }: DndEndCallbackDetail) {
    if (!active || !over) return;
    const item = String(active.id);
    const overData = over.data as { type?: string; container?: boolean } | undefined;
    const at = position === "top" ? "top" : "bottom";

    let target: DropTarget;
    if (overData?.type === "unused") {
      // Anywhere in the unused column removes the item from the order
      target = { kind: "unused" };
    } else if (overData?.type === "selected") {
      target = overData.container
        ? { kind: "selected", position: at }
        : { kind: "selected", itemId: String(over.id), position: at };
    } else {
      return;
    }
    draft = applyDrop(draft, item, target);
  }

  function save() {
    if (isDirty) onSave([...draft]);
    open = false;
  }

  function cancel() {
    if (isDirty) {
      toast.info(t.Input.FilterOrderReverted(), toastOptions);
    }
    open = false;
  }

  // Every close request that comes from the dialog itself (escape, overlay
  // click, close button) is a cancel; saving closes the dialog
  // programmatically, which does not pass through here
  function onOpenChange(next: boolean) {
    if (!next) cancel();
  }
</script>

<Dialog bind:open {onOpenChange}>
  <!-- The dnd provider sits OUTSIDE the dialog content on purpose: the content
       is a fixed, transform-centered box, and a fixed positioned drag overlay
       inside it would be positioned against that box instead of the viewport,
       offsetting the overlay and the drop math by the dialog position -->
  <DndProvider orientation="vertical" {dndRules} {onDndEnd}>
    {#snippet children({ dropIndicator })}
      <DialogContent class="flex h-[640px] max-h-[calc(100vh-2rem)] flex-col gap-6 sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle>{displayName}</DialogTitle>
        </DialogHeader>
        <div class="grid min-h-0 flex-1 grid-cols-2 gap-4">
          <!-- Selected: the user order, every row carries the up / down /
               remove buttons, dragging is an alternative to them -->
          <div data-slot="filter-order-column" data-column="selected" class="flex min-h-0 min-w-0 flex-col gap-y-1.5">
            <div class="text-muted-foreground border-border border-b pb-1.5 text-xs font-medium">
              {t.Input.FilterOrderSelected()}
            </div>
            <FilterOrderColumn column="selected">
              {#if draft.length === 0}
                <div class="text-muted-foreground flex min-h-16 items-center justify-center px-2 text-center text-xs">
                  {t.Input.FilterOrderSelectedEmpty()}
                </div>
              {:else}
                {#each draft as item, index (item)}
                  <FilterOrderItem
                    {item}
                    column="selected"
                    {index}
                    count={draft.length}
                    {dropIndicator}
                    invalid={isInvalid(item)}
                    label={labelOf(item)}
                    onMoveUp={() => onMoveUp(item)}
                    onMoveDown={() => onMoveDown(item)}
                    onRemove={() => onRemove(item)}
                  />
                {/each}
              {/if}
            </FilterOrderColumn>
          </div>

          <!-- Unused: the rest of "option", its order is always the option
               order, a drop on it only removes the item from the order -->
          <div data-slot="filter-order-column" data-column="unused" class="flex min-h-0 min-w-0 flex-col gap-y-1.5">
            <div class="text-muted-foreground border-border border-b pb-1.5 text-xs font-medium">
              {t.Input.FilterOrderUnused()}
            </div>
            <FilterOrderColumn column="unused">
              {#if unused.length === 0}
                <div class="text-muted-foreground flex min-h-16 items-center justify-center px-2 text-center text-xs">
                  {t.Input.FilterOrderUnusedEmpty()}
                </div>
              {:else}
                {#each unused as item (item)}
                  <FilterOrderItem {item} column="unused" label={labelOf(item)} onAdd={() => onAdd(item)} />
                {/each}
              {/if}
            </FilterOrderColumn>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onclick={cancel}>{t.Input.Cancel()}</Button>
          <Button onclick={save}>{t.Input.Save()}</Button>
        </DialogFooter>
      </DialogContent>
    {/snippet}

    {#snippet dragOverlay({ active })}
      {#if active}
        <div class="bg-popover flex w-64 items-center gap-x-1.5 rounded-md border px-2 py-1.5 text-sm shadow-xl">
          <GripVertical class="text-muted-foreground size-4" />
          <span class="truncate">{labelOf(String(active.id))}</span>
        </div>
      {/if}
    {/snippet}
  </DndProvider>
</Dialog>
