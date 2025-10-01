<script lang="ts">
  import { Folder } from "@lucide/svelte";
  import { cn } from "$lib/utils.js";

  let {
    name,
    onOpen,
  }: {
    name: string;
    onOpen?: () => void;
  } = $props();

  /**
   * Handle double-click to open folder.
   * This matches the standard file explorer behavior across operating systems,
   * where double-clicking opens a folder rather than single-clicking.
   */
  function handleDoubleClick() {
    onOpen?.();
  }

  /**
   * Handle keyboard interaction for accessibility.
   * When the folder is focused, pressing Enter should open it, matching
   * the behavior of native file explorers where Enter opens selected items.
   */
  function handleKeyDown(event: KeyboardEvent) {
    if (event.key === "Enter") {
      onOpen?.();
    }
  }
</script>

<div
  role="button"
  tabindex="0"
  ondblclick={handleDoubleClick}
  onkeydown={handleKeyDown}
  aria-label={`Open folder ${name}`}
  class={cn(
    "group border-border relative aspect-square w-full rounded-lg border-2",
    "bg-card hover:border-primary transition-all duration-200 hover:shadow-md",
    "flex cursor-pointer flex-col items-center justify-center p-4",
    "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
  )}
>
  <div class="mb-2">
    <Folder
      class={cn("text-muted-foreground group-hover:text-primary h-16 w-16 transition-colors")}
      strokeWidth={1.5}
    />
  </div>

  <div class="w-full text-center">
    <p
      class={cn(
        "text-card-foreground group-hover:text-primary text-sm font-medium",
        "line-clamp-2 break-all transition-colors",
      )}
    >
      {name}
    </p>
  </div>

  <div
    class={cn(
      "bg-primary/5 absolute inset-0 rounded-lg opacity-0",
      "pointer-events-none transition-opacity group-hover:opacity-100",
    )}
  ></div>
</div>
