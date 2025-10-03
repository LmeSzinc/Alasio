<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";

  let {
    name,
    content,
    badge,
    onclick,
    ondblclick,
    class: className,
  }: {
    name: string;
    content?: Snippet;
    badge?: Snippet;
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
    "group relative aspect-square w-full rounded-lg border-2",
    "bg-card cursor-pointer overflow-hidden transition-all duration-200 hover:shadow-md",
    "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
    className,
  )}
>
  <!-- Content Area -->
  <div class="relative h-full w-full">
    {#if content}
      {@render content()}
    {:else}
      <div class="absolute inset-0 flex items-center justify-center">
        <div class="text-muted-foreground text-sm">No content</div>
      </div>
    {/if}
  </div>

  <!-- Badge Area (top-right) -->
  {#if badge}
    <div class="absolute top-2 right-2">
      {@render badge()}
    </div>
  {/if}

  <!-- Name Label (bottom) -->
  <div class="absolute right-0 bottom-0 left-0 p-2">
    <p
      class={cn(
        "text-card-foreground text-xs font-medium group-hover:text-white",
        "bg-card/90 rounded px-2 py-1 group-hover:bg-transparent",
        "line-clamp-2 break-all transition-all",
      )}
    >
      {name}
    </p>
  </div>
</div>
