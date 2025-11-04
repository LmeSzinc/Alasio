<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import RenameTextarea from "./RenameTextarea.svelte";
  import { assetSelection, type AssetSelectionItem } from "./selected.svelte";

  let {
    name,
    item,
    content,
    handleSelect,
    handleOpen,
    handleRename,
    class: className,
    ...restProps
  }: {
    name: string;
    item: AssetSelectionItem;
    content?: Snippet;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    handleRename?: (oldName: string, newName: string) => void;
    class?: string;
  } & HTMLAttributes<HTMLDivElement> = $props();

  const selected = $derived(assetSelection.isSelected(item));
  const isRenaming = $derived(assetSelection.isRenaming(item));

  let gridcellElement: HTMLDivElement | null = $state(null);

  function onclick(event: MouseEvent) {
    if (isRenaming) {
      // Exit rename mode without calling onSubmit (no change)
      // letting RenameTextarea blur event to handle it
      return;
    }
    // Handle selection first
    handleSelect?.(event);
    // Ensure gridcell gets focus after selection
    setTimeout(() => {
      if (gridcellElement && document.activeElement !== gridcellElement) {
        gridcellElement.focus();
      }
    }, 0);
  }

  function ondblclick() {
    if (isRenaming) {
      return;
    }
    handleOpen?.();
  }
  // Prevent name selected on double-click open
  function onmousedown(event: MouseEvent) {
    if (assetSelection.renamingItem) {
      return;
    }
    event.preventDefault();
  }

  function onSubmit(oldName: string, newName: string) {
    handleRename?.(oldName, newName);
  }
</script>

<div
  role="gridcell"
  tabindex="-1"
  bind:this={gridcellElement}
  {onclick}
  {ondblclick}
  {onmousedown}
  aria-label={name}
  class={cn(
    "group flex w-full cursor-pointer items-center justify-between rounded-md p-1",
    "overflow-hidden shadow-sm outline-none",
    "text-card-foreground font-consolas text-xs",
    selected ? "bg-primary text-white" : "bg-card hover:bg-card/80",
    className,
  )}
  {...restProps}
>
  <!-- Left: Asset Name (full display, no truncation) -->
  <div class="min-w-0 flex-1 px-2">
    <RenameTextarea {name} {isRenaming} selectionState={assetSelection} {gridcellElement} {onSubmit} class="!text-left">
      <span class={cn("break-all", selected ? "text-white" : "group-hover:text-primary transition-colors")}>
        {name}
      </span>
    </RenameTextarea>
  </div>

  <!-- Right: Template Image (fixed width) -->
  {#if content}
    <div class="flex-shrink-0">
      {@render content()}
    </div>
  {/if}
</div>
