<script lang="ts">
  import { toast } from "svelte-sonner";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import { type ArgData, getArgName, i18nTextToOneLine, useArgValue } from "$lib/components/arg/utils.svelte";
  import { type DndEndCallbackDetail, DndProvider } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import { belowDragBand } from "$lib/window/dragRegion.svelte";
  import FilterOrderColumn from "./FilterOrderColumn.svelte";
  import FilterOrderItem from "./FilterOrderItem.svelte";
  import {
    type DropTarget,
    type FilterOrderItem as FilterOrderItemValue,
    SECONDARY_SEPARATOR,
    appendItem,
    applyDrop,
    composeOption,
    defaultItem,
    moveItem,
    primaryOf,
    removeItem,
    sameList,
    splitOption,
    validItems,
  } from "./filterOrder";

  /**
   * The transfer-like editor of dt="filter-order" (see
   * doc/2026-10-03_filter-order.md §3.2).
   *
   * Every edit only touches the local `draft`; the value is committed through
   * `onSave` when the user presses save with a changed order, and discarded
   * with an "edits discarded" toast when the dialog is cancelled.
   *
   * The reset button is not a draft operation: it resets the whole arg to its
   * default through `onReset` (the single-arg reset of the backend, the same
   * as the reset button of dt=input) and closes the dialog, the draft has no
   * meaning once the value was replaced.
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

  // The unused column is the rest of "option", it always follows the option
  // order, so a drag inside it has no visual effect. With secondary options
  // the column lists the primary items, a primary that is in the order (with
  // any secondary option) is not offered again.
  const options = $derived(Array.isArray(data.option) ? data.option : []);
  const optionDict = $derived(data.option_dict ?? {});
  const hasSecondary = $derived(Object.keys(optionDict).length > 0);
  const valid = $derived(validItems(options, optionDict));

  /**
   * The primary item of a row: the item itself when the arg declares no
   * secondary options, because such an item is never split. A plain item may
   * hold the level separator ("Fleet-1"), so splitting it would read the wrong
   * primary and offer used items again.
   */
  function primaryOfRow(item: FilterOrderItemValue): string {
    return hasSecondary ? primaryOf(String(item)) : String(item);
  }

  const usedPrimaries = $derived(new Set(draft.map(primaryOfRow)));
  const unused = $derived(options.filter((item) => !usedPrimaries.has(String(item))));

  /**
   * True for an item that is in the order but not a legal item anymore: its
   * primary item or its secondary option was removed from the yaml, or a
   * primary that declares secondary options is stored bare.
   */
  function isInvalid(item: FilterOrderItemValue): boolean {
    if (hasSecondary) return !valid.has(String(item));
    return options.length > 0 && !options.includes(item);
  }

  /**
   * Label of a whole item, the translated secondary option after the level
   * separator like the stored value ("PlateT4-gun"). A row itself shows its
   * primary item and the selector next to it, the drag overlay has room for
   * the whole item.
   */
  function fullLabelOf(item: FilterOrderItemValue): string {
    if (!hasSecondary) return arg.getLabel(item);
    const [primary, secondary] = splitOption(String(item));
    if (secondary === null) return arg.getLabel(primary);
    return `${arg.getLabel(primary)}${SECONDARY_SEPARATOR}${arg.getLabel(secondary)}`;
  }

  /** The secondary options the primary item of a row declares */
  function secondariesOf(item: FilterOrderItemValue): string[] {
    return optionDict[primaryOfRow(item)] ?? [];
  }

  /** The secondary option the row carries, null when it carries none */
  function secondaryOf(item: FilterOrderItemValue): string | null {
    return hasSecondary ? splitOption(String(item))[1] : null;
  }

  /** True when the row carries a secondary option that is not declared anymore */
  function isSecondaryInvalid(item: FilterOrderItemValue): boolean {
    const secondary = secondaryOf(item);
    return hasSecondary && secondary !== null && !secondariesOf(item).includes(secondary);
  }

  const hasInvalid = $derived(draft.some((item) => isInvalid(item)));

  const isDirty = $derived(!sameList(draft, value));

  const dndRules = { selected: ["item"], unused: ["item"] };

  // The Toaster keeps every toast below the app header, so only the lifetime
  // is set here
  const toastOptions = {
    duration: 2000,
  };

  function labelOf(item: FilterOrderItemValue): string {
    // A row shows its primary item, the secondary option has its own selector
    return arg.getLabel(hasSecondary ? primaryOf(String(item)) : item);
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

  /** A row added from the unused column starts at the first secondary option */
  function onAdd(item: FilterOrderItemValue) {
    draft = appendItem(draft, defaultItem(String(item), optionDict));
  }

  /** Replace the secondary option of a row, the position of the row is kept */
  function onSecondaryChange(item: FilterOrderItemValue, next: string) {
    const index = draft.indexOf(item);
    if (index === -1) return;
    const nextItem = composeOption(primaryOfRow(item), next);
    if (nextItem === item) return;
    draft = [...draft.slice(0, index), nextItem, ...draft.slice(index + 1)];
  }

  /**
   * A drag starts with the id of the row: an unused row carries its primary
   * item only, so it is completed with its first secondary option before the
   * drop, the same as adding it with the plus button.
   */
  function normalizeDragged(id: string): string {
    if (!hasSecondary || draft.includes(id)) return id;
    return defaultItem(primaryOf(id), optionDict);
  }

  function onDndEnd({ active, over, position }: DndEndCallbackDetail) {
    if (!active || !over) return;
    const item = normalizeDragged(String(active.id));
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
    if (hasInvalid) return;
    if (isDirty) onSave([...draft]);
    open = false;
  }

  function cancel() {
    if (isDirty) {
      toast.info(t.Input.FilterOrderReverted(), toastOptions);
    }
    open = false;
  }

  /**
   * Reset the arg to its default value (see the header comment): a committed
   * change of the whole arg, unlike save / cancel which only deal with the
   * draft.
   */
  function resetDefault() {
    onReset?.();
    open = false;
  }

  // Every close request that comes from the dialog itself (escape, overlay
  // click, close button) is a cancel; saving closes the dialog
  // programmatically, which does not pass through here
  function onOpenChange(next: boolean) {
    if (!next) cancel();
  }

  // The dialog is centred below the drag band of the desktop client, so its
  // title row stays clickable instead of sitting under the window drag strip
  const band = $derived(belowDragBand());
</script>

<Dialog bind:open {onOpenChange}>
  <!-- The dnd provider sits OUTSIDE the dialog content on purpose: the content
       is a fixed, transform-centered box, and a fixed positioned drag overlay
       inside it would be positioned against that box instead of the viewport,
       offsetting the overlay and the drop math by the dialog position -->
  <DndProvider orientation="vertical" {dndRules} {onDndEnd}>
    {#snippet children({ dropIndicator })}
      <!-- The base class centres the dialog in the viewport; `top` and
           `max-height` move it below the drag band of the desktop client -->
      <DialogContent
        class="flex h-[640px] flex-col gap-6 sm:max-w-[720px]"
        style="top: {band.top}; max-height: {band.maxHeight};"
      >
        <DialogHeader>
          <DialogTitle>{displayName}</DialogTitle>
        </DialogHeader>
        <div class="grid min-h-0 flex-1 grid-cols-2 gap-4">
          <!-- Selected: the user order, every row carries the up / down /
               remove buttons, dragging is an alternative to them -->
          <div data-slot="filter-order-column" data-column="selected" class="flex min-h-0 min-w-0 flex-col gap-y-1.5">
            <div class="border-b border-border pb-1.5 text-xs font-medium text-muted-foreground">
              {t.Input.FilterOrderSelected()}
            </div>
            <FilterOrderColumn column="selected">
              {#if draft.length === 0}
                <div class="flex min-h-16 items-center justify-center px-2 text-center text-xs text-muted-foreground">
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
                    secondary={secondaryOf(item)}
                    secondaries={secondariesOf(item)}
                    secondaryInvalid={isSecondaryInvalid(item)}
                    secondaryLabel={(value) => arg.getLabel(value)}
                    onSecondaryChange={(next) => onSecondaryChange(item, next)}
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
            <div class="border-b border-border pb-1.5 text-xs font-medium text-muted-foreground">
              {t.Input.FilterOrderUnused()}
            </div>
            <FilterOrderColumn column="unused">
              {#if unused.length === 0}
                <div class="flex min-h-16 items-center justify-center px-2 text-center text-xs text-muted-foreground">
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
          <Button variant="ghost" onclick={resetDefault}>{t.Input.FilterOrderResetDefault()}</Button>
          <Button variant="outline" onclick={cancel}>{t.Input.Cancel()}</Button>
          <Button onclick={save} disabled={hasInvalid}>{t.Input.Save()}</Button>
        </DialogFooter>
      </DialogContent>
    {/snippet}

    {#snippet dragOverlay({ active })}
      {#if active}
        <div class="flex w-64 items-center gap-x-1.5 rounded-md border bg-popover px-2 py-1.5 text-sm shadow-xl">
          <GripVertical class="size-4 text-muted-foreground" />
          <span class="truncate">{fullLabelOf(String(active.id))}</span>
        </div>
      {/if}
    {/snippet}
  </DndProvider>
</Dialog>
