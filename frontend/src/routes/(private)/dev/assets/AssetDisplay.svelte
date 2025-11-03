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

  let containerElement: HTMLDivElement | null = $state(null);

  function handleClick(event: MouseEvent) {
    // Ensure container gets focus
    if (containerElement && document.activeElement !== containerElement) {
      containerElement.focus();
    }

    if (isRenaming) {
      // Submit rename on click outside textarea
      handleRenameSubmit(name, name);
      return;
    }
    handleSelect?.(event);
  }

  function handleDoubleClick(event: MouseEvent) {
    if (isRenaming) {
      return;
    }
    handleOpen?.();
  }

  function handleRenameSubmit(oldName: string, newName: string) {
    handleRename?.(oldName, newName);
  }
</script>

<div
  role="button"
  tabindex="-1"
  bind:this={containerElement}
  onclick={handleClick}
  ondblclick={handleDoubleClick}
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
    <RenameTextarea
      {name}
      {isRenaming}
      selectionState={assetSelection}
      gridcellElement={containerElement}
      onSubmit={handleRenameSubmit}
      class="!text-left"
    >
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
