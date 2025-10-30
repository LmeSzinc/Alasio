<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";

  let {
    name,
    content,
    badge,
    selected,
    handleSelect,
    handleOpen,
    class: className,
  }: {
    name: string;
    content?: Snippet;
    badge?: Snippet;
    selected?: boolean;
    handleSelect?: (event: MouseEvent) => void;
    handleOpen?: () => void;
    class?: string;
  } = $props();

  function handleMouseDown(event: MouseEvent) {
    event.preventDefault();
  }
  function handleClick(event: MouseEvent) {
    event.preventDefault();
    handleSelect?.(event);
  }
  function handleDoubleClick(event: MouseEvent) {
    event.preventDefault();
    handleOpen?.();
  }

  /**
   * Handle keyboard interaction for accessibility.
   * Pressing Enter triggers the primary action (double-click if available, otherwise single-click).
   */
  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      if (handleOpen) {
        handleOpen();
      } else if (handleSelect) {
        // Create a synthetic MouseEvent for keyboard activation
        const mouseEvent = new MouseEvent("click", {
          ctrlKey: event.ctrlKey,
          shiftKey: event.shiftKey,
          metaKey: event.metaKey,
        });
        handleSelect(mouseEvent);
      }
    }
  }
</script>

<div
  role="button"
  tabindex="0"
  onclick={handleClick}
  ondblclick={handleDoubleClick}
  onkeydown={handleKeyDown}
  onmousedown={handleMouseDown}
  aria-label={name}
  class={cn(
    "group relative aspect-square h-32 w-32",
    "cursor-pointer overflow-hidden outline-none",
    "hover:bg-card rounded-md hover:shadow-md",
    "flex flex-col border-2",
    selected ? "border-primary" : "border-transparent",
    className,
  )}
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
      <div class="absolute top-2 right-2">
        {@render badge()}
      </div>
    {/if}
  </div>

  <!-- Name Label (bottom) -->
  <div class="items-top flex flex-1 justify-center">
    <p
      class={cn(
        "text-card-foreground font-consolas text-center text-xs",
        "line-clamp-2 break-all transition-colors group-hover:text-primary",
      )}
    >
      {name}
    </p>
  </div>
</div>
