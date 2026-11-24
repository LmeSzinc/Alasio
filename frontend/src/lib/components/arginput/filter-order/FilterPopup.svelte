<script lang="ts">
  import type { Active } from "@dnd-kit-svelte/core";
  import type { Snippet } from "svelte";
  import { toast } from "svelte-sonner";
  import GripVertical from "@lucide/svelte/icons/grip-vertical";
  import { type DndEndCallbackDetail, DndProvider, type DndRules, type DropIndicatorState } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { t } from "$lib/i18n";
  import { belowDragBand } from "$lib/window/dragRegion.svelte";

  /**
   * The shell of every ordered filter editor: the dialog, its title row, the
   * drag and drop provider, the row overlay and the footer buttons.
   *
   * The editors only own their draft: they hand over the body (one entry with
   * the drop indicator) and the `dirty` / `blocked` state, and the save, the
   * cancel and the reset behave the same for all of them. A cancel with a
   * changed draft says so with a toast, the reset replaces the whole arg through
   * the single-arg reset of the backend and closes the dialog.
   */
  type Props = {
    open?: boolean;
    /** Title of the dialog, a text of multiple lines is flattened by the caller */
    title: string;
    /** True when the draft differs from the value, only a dirty draft is saved */
    dirty?: boolean;
    /** True when the draft can not be saved, e.g. an invalid or a repeated item */
    blocked?: boolean;
    /** Which active row types each column accepts */
    dndRules: DndRules;
    onDndEnd: (detail: DndEndCallbackDetail) => void;
    /** Commits the draft, called only when it is dirty and not blocked */
    onSave: () => void;
    /** Resets the whole arg to its default value */
    onReset?: () => void;
    /** The body of the dialog, the rows read the drop indicator from it */
    children: Snippet<[{ dropIndicator: DropIndicatorState }]>;
    /** The label of the row being dragged, shown in the drag overlay */
    overlayLabel: (item: string) => string;
  };
  let {
    open = $bindable(false),
    title,
    dirty = false,
    blocked = false,
    dndRules,
    onDndEnd,
    onSave,
    onReset,
    children,
    overlayLabel,
  }: Props = $props();

  function save() {
    if (blocked) return;
    if (dirty) onSave();
    open = false;
  }

  function cancel() {
    if (dirty) {
      toast.info(t.Input.FilterOrderReverted(), { duration: 2000 });
    }
    open = false;
  }

  function resetDefault() {
    onReset?.();
    open = false;
  }

  // Every close request that comes from the dialog itself (escape, overlay
  // click, close button) is a cancel; saving closes the dialog programmatically,
  // which does not pass through here
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
    {#snippet children({ dropIndicator }: { dropIndicator: DropIndicatorState })}
      <!-- The base class centres the dialog in the viewport; `top` and
           `max-height` move it below the drag band of the desktop client -->
      <DialogContent
        class="flex h-[640px] flex-col gap-6 sm:max-w-[720px]"
        style="top: {band.top}; max-height: {band.maxHeight};"
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {@render children({ dropIndicator })}
        <DialogFooter>
          <Button variant="ghost" onclick={resetDefault}>{t.Input.FilterOrderResetDefault()}</Button>
          <Button variant="outline" onclick={cancel}>{t.Input.Cancel()}</Button>
          <Button onclick={save} disabled={blocked}>{t.Input.Save()}</Button>
        </DialogFooter>
      </DialogContent>
    {/snippet}

    {#snippet dragOverlay({ active }: { active: Active | null })}
      {#if active}
        <div class="flex w-64 items-center gap-x-1.5 rounded-md border bg-popover px-2 py-1.5 text-sm shadow-xl">
          <GripVertical class="size-4 text-muted-foreground" />
          <span class="truncate">
            {overlayLabel(String((active.data as { item?: string } | undefined)?.item ?? ""))}
          </span>
        </div>
      {/if}
    {/snippet}
  </DndProvider>
</Dialog>
