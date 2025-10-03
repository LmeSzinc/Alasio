<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";

  let {
    name,
    content,
    badge,
    selected,
    onclick,
    ondblclick,
    class: className,
  }: {
    name: string;
    content?: Snippet;
    badge?: Snippet;
    selected?: boolean;
    onclick?: () => void;
    ondblclick?: () => void;
    class?: string;
  } = $props();

  function handleClick() {
    onclick?.();
  }
  function handleDoubleClick() {
    ondblclick?.();
  }

  /**
   * Handle keyboard interaction for accessibility.
   * Pressing Enter triggers the primary action (double-click if available, otherwise single-click).
   */
  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      if (ondblclick) {
        ondblclick();
      } else if (onclick) {
        onclick();
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
  aria-label={name}
  class={cn(
    "group relative aspect-square w-32",
    "cursor-pointer overflow-hidden transition-all duration-200",
    "hover:bg-card hover:shadow-md",
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
    <!-- Background highlight effect on hover -->
    <div
      class={cn(
        "bg-primary/5 absolute inset-0 opacity-0",
        "pointer-events-none transition-opacity group-hover:opacity-100",
      )}
    ></div>

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
        "line-clamp-2 break-all transition-colors",
        "group-hover:text-primary",
      )}
    >
      {name}
    </p>
  </div>
</div>
