<script lang="ts">
  import { Textarea } from "$lib/components/ui/textarea";
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import { untrack } from "svelte";
  import type { HTMLAttributes, MouseEventHandler } from "svelte/elements";
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

  let editValue = $state(name);
  let textareaElement: HTMLTextAreaElement | null = $state(null);
  let gridcellElement: HTMLDivElement | null = $state(null);
  let isComposing = $state(false);

  // Focus and select text when entering rename mode
  $effect(() => {
    if (isRenaming && textareaElement) {
      // Use untrack to read editValue without subscribing to its changes
      // This prevents the effect from re-running when user types
      const currentValue = untrack(() => editValue);

      // Use setTimeout to ensure focus is fully complete before selecting text
      // This prevents the selection from being immediately cleared
      setTimeout(() => {
        if (!textareaElement) return;

        // Auto-resize textarea to fit FULL content before focusing
        // This ensures the entire text is visible from the start
        adjustTextareaHeight();

        textareaElement.focus();
        // Select filename without extension
        const lastDotIndex = currentValue.lastIndexOf(".");
        if (lastDotIndex > 0) {
          textareaElement.setSelectionRange(0, lastDotIndex);
        } else {
          textareaElement.select();
        }
      }, 0);
    } else if (!isRenaming) {
      // Reset when exiting rename mode
      editValue = name;
    }
  });

  // Update edit value when name changes (but only when NOT renaming)
  $effect(() => {
    // Track name changes
    name;
    // Use untrack to read isRenaming without subscribing
    untrack(() => {
      if (!isRenaming) {
        editValue = name;
      }
    });
  });

  /**
   * Adjust textarea height to fit content
   * This properly calculates height based on the CURRENT content,
   * ensuring the entire text is visible even when cursor is at the beginning
   */
  function adjustTextareaHeight() {
    if (!textareaElement) return;
    // Reset height to auto to get the true scrollHeight
    textareaElement.style.height = "auto";
    // Calculate the proper height based on scrollHeight
    // This ensures all content is visible, not just what fits in the current viewport
    const newHeight = textareaElement.scrollHeight;
    // Set the height to show all content
    textareaElement.style.height = newHeight + "px";
  }

  function handleMouseDown(event: MouseEvent) {
    if (!isRenaming) {
      event.preventDefault();
    }
  }
  function handleClick(event: MouseEvent) {
    if (isRenaming) {
      return;
    }
    event.preventDefault();
    handleSelect?.(event);
  }
  function handleDoubleClick(event: MouseEvent) {
    if (isRenaming) {
      return;
    }
    event.preventDefault();
    handleOpen?.();
  }

  // Merge restProps' onclick with internal handleClick
  const mergedOnClick: MouseEventHandler<HTMLDivElement> = (event) => {
    // First call external onclick if exists (e.g., handleItemClick for focus)
    if (restProps.onclick) {
      restProps.onclick(event);
    }
    // Then call internal click handler
    if (isRenaming) {
      // If we are renaming, a click on the component (but not the textarea itself,
      // because its own click handler stops propagation) should exit rename mode.
      // We do this by blurring the textarea, which triggers our existing onblur logic.
      submitRename();
    } else {
      // If not renaming, perform the standard selection click.
      handleClick(event);
    }
  };
  // Merge restProps' ondblclick with internal handleDoubleClick
  const mergedOnDoubleClick: MouseEventHandler<HTMLDivElement> = (event) => {
    if (restProps.ondblclick) {
      restProps.ondblclick(event);
    }
    if (!isRenaming) {
      handleDoubleClick(event);
    }
  };
  // Merge restProps' onmousedown with internal handleMouseDown
  const mergedOnMouseDown: MouseEventHandler<HTMLDivElement> = (event) => {
    if (restProps.onmousedown) {
      restProps.onmousedown(event);
    }
    handleMouseDown(event);
  };
  // Extract event handlers from restProps to prevent duplication
  const { onclick, ondblclick, onmousedown, onkeydown, ...otherRestProps } = restProps;

  /**
   * Handle textarea keyboard events
   */
  function handleTextareaKeyDown(event: KeyboardEvent) {
    if (isComposing) {
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      submitRename();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelRename();
      return;
    }
  }

  /**
   * Handle textarea input to auto-resize
   * This is called on every input event to adjust height as user types
   */
  function handleTextareaInput() {
    adjustTextareaHeight();
  }

  /**
   * Handle blur event - check where focus is going
   */
  function handleTextareaBlur(event: FocusEvent) {
    // If we're no longer in renaming mode, ignore
    if (!isRenaming) {
      return;
    }
    // Check if we're in the initialization period
    // This handles the case where context menu closes and briefly restores focus
    // to the container before the textarea gets focus
    if (resourceSelection.isRenamingInitializing()) {
      return;
    }
    // If textarea regained focus, ignore this blur
    if (event.relatedTarget === textareaElement || event.relatedTarget === gridcellElement) {
      return;
    }
    // Focus has truly left the textarea, submit the rename
    submitRename();
  }

  /**
   * Handle IME composition events
   */
  function handleCompositionStart() {
    isComposing = true;
  }
  function handleCompositionEnd() {
    isComposing = false;
  }

  /**
   * Submit the rename
   */
  function submitRename() {
    const trimmedValue = editValue.trim();
    if (trimmedValue && trimmedValue !== name) {
      handleRename?.(name, trimmedValue);
    }
    resourceSelection.stopRenaming();
    gridcellElement?.focus();
  }
  function cancelRename() {
    editValue = name;
    resourceSelection.stopRenaming();
    gridcellElement?.focus();
  }

  /**
   * Prevent mousedown on textarea to trigger parent-level handlers
   * So text can be selected
   */
  function handleTextareaMouseDown(event: MouseEvent) {
    event.stopPropagation();
  }
  function handleTextareaClick(event: MouseEvent) {
    event.stopPropagation();
  }
</script>

<div
  role="gridcell"
  tabindex="-1"
  bind:this={gridcellElement}
  onclick={mergedOnClick}
  ondblclick={mergedOnDoubleClick}
  onmousedown={mergedOnMouseDown}
  {onkeydown}
  aria-label={name}
  class={cn(
    "group relative aspect-square h-27 w-27",
    "cursor-pointer overflow-hidden outline-none",
    "hover:bg-card rounded-md hover:shadow-md",
    "flex flex-col border-2",
    selected ? "border-primary" : "border-transparent",
    className,
  )}
  {...otherRestProps}
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
    {#if isRenaming}
      <!-- Inline editing textarea -->
      <Textarea
        bind:ref={textareaElement}
        bind:value={editValue}
        onkeydown={handleTextareaKeyDown}
        oninput={handleTextareaInput}
        onblur={handleTextareaBlur}
        onmousedown={handleTextareaMouseDown}
        onclick={handleTextareaClick}
        oncompositionstart={handleCompositionStart}
        oncompositionend={handleCompositionEnd}
        class={cn(
          "w-full resize-none overflow-hidden",
          // match fontsize of normal display, no transition for faster focus
          "text-card-foreground !font-consolas text-center !text-xs break-all transition-none",
          "border-primary bg-background border-2",
          "min-h-0 p-0.5 leading-tight",
        )}
        style="overflow-wrap: break-word; word-break: break-all;"
      />
    {:else}
      <!-- Normal display -->
      <p
        class={cn(
          "text-card-foreground font-consolas text-center text-xs",
          "group-hover:text-primary line-clamp-2 break-all transition-colors",
        )}
      >
        {name}
      </p>
    {/if}
  </div>
</div>
