<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import RenameTextarea from "./RenameTextarea.svelte";
  import { resourceSelection, type ResourceSelectionItem } from "./selected.svelte";

  let {
    name,
    item,
    content,
    badge,
    handleSelect,
    handleOpen,
    handleRename,
    class: className,
    ...restProps
  }: {
    name: string;
    item: ResourceSelectionItem;
    content?: Snippet;
    badge?: Snippet;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    handleRename?: (oldName: string, newName: string) => void;
    class?: string;
  } & HTMLAttributes<HTMLDivElement> = $props();

  const selected = $derived(resourceSelection.isSelected(item));
  const isRenaming = $derived(resourceSelection.isRenaming(item));

  let gridcellElement: HTMLDivElement | null = $state(null);

  function handleClick(event: MouseEvent) {
    // Ensure gridcell gets focus
    if (gridcellElement && document.activeElement !== gridcellElement) {
      gridcellElement.focus();
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
  role="gridcell"
  tabindex="-1"
  bind:this={gridcellElement}
  onclick={handleClick}
  ondblclick={handleDoubleClick}
  aria-label={name}
  class={cn(
    "group relative aspect-square h-27 w-27",
    "cursor-pointer overflow-hidden outline-none",
    "hover:bg-card rounded-md hover:shadow-md",
    "flex flex-col border-2",
    selected ? "border-primary" : "border-transparent",
    className,
  )}
  {...restProps}
>
  <!-- Content Area -->
  <div class="relative w-full" style="height: 75%;">
    {#if content}
      <div class="h-full w-full overflow-hidden">
        {@render content()}
      </div>
    {:else}
      <div class="flex h-full w-full items-center justify-center">
        <div class="text-muted-foreground text-sm">No content</div>
      </div>
    {/if}

    <!-- Badge Area (top-right) -->
    {#if badge}
      <div class="absolute top-1 right-1">
        {@render badge()}
      </div>
    {/if}
  </div>

  <!-- Name Label (bottom) -->
  <div class="items-top flex flex-1 justify-center px-1">
    <RenameTextarea
      {name}
      {isRenaming}
      selectionState={resourceSelection}
      {gridcellElement}
      onSubmit={handleRenameSubmit}
    >
      <p
        class={cn(
          "text-card-foreground font-consolas text-center text-xs",
          "group-hover:text-primary line-clamp-2 break-all transition-colors",
        )}
      >
        {name}
      </p>
    </RenameTextarea>
  </div>
</div>
