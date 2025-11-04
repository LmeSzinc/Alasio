<script lang="ts">
  import { Textarea } from "$lib/components/ui/textarea";
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import { untrack } from "svelte";

  let {
    name,
    isRenaming = false,
    onSubmit,
    onCancel,
    selectionState,
    gridcellElement,
    children,
    class: className,
  }: {
    name: string;
    isRenaming?: boolean;
    onSubmit?: (oldName: string, newName: string) => void;
    onCancel?: () => void;
    selectionState: {
      isRenamingInitializing: () => boolean;
      stopRenaming: () => void;
    };
    gridcellElement?: HTMLDivElement | null;
    children?: Snippet;
    class?: string;
  } = $props();

  let editValue = $state(name);
  let textareaElement: HTMLTextAreaElement | null = $state(null);
  let isComposing = $state(false);

  // Focus and select text when entering rename mode
  $effect(() => {
    if (isRenaming && textareaElement) {
      const currentValue = untrack(() => editValue);

      setTimeout(() => {
        if (!textareaElement) return;

        // Auto-resize textarea to fit FULL content before focusing
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
   */
  function adjustTextareaHeight() {
    if (!textareaElement) return;
    textareaElement.style.height = "auto";
    const newHeight = textareaElement.scrollHeight;
    textareaElement.style.height = newHeight + "px";
  }

  /**
   * Handle textarea keyboard events
   */
  function handleKeyDown(event: KeyboardEvent) {
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
   */
  function handleInput() {
    adjustTextareaHeight();
  }

  /**
   * Handle blur event - check where focus is going
   */
  function handleBlur(event: FocusEvent) {
    if (!isRenaming) {
      return;
    }
    // Check if we're in the initialization period
    if (selectionState.isRenamingInitializing()) {
      return;
    }
    // If textarea or gridcell regained focus, ignore this blur
    if (event.relatedTarget === textareaElement) {
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
    // Only call onSubmit if value changed
    if (trimmedValue && trimmedValue !== name) {
      onSubmit?.(name, trimmedValue);
    }
    selectionState.stopRenaming();
    gridcellElement?.focus();
  }

  /**
   * Cancel the rename
   */
  function cancelRename() {
    editValue = name;
    selectionState.stopRenaming();
    gridcellElement?.focus();
    onCancel?.();
  }

  /**
   * Prevent mousedown/click on textarea to trigger parent-level handlers
   */
  function handleMouseDown(event: MouseEvent) {
    event.stopPropagation();
  }
  function handleClick(event: MouseEvent) {
    event.stopPropagation();
  }
</script>

{#if isRenaming}
  <Textarea
    bind:ref={textareaElement}
    bind:value={editValue}
    onkeydown={handleKeyDown}
    oninput={handleInput}
    onblur={handleBlur}
    onmousedown={handleMouseDown}
    onclick={handleClick}
    oncompositionstart={handleCompositionStart}
    oncompositionend={handleCompositionEnd}
    class={cn(
      "w-full resize-none overflow-hidden",
      "text-card-foreground !font-consolas text-center !text-xs break-all transition-none",
      "!border-none px-0 py-0.5 rounded-xs bg-background",
      "min-h-0 leading-tight",
      className,
    )}
    style="overflow-wrap: break-word; word-break: break-all;"
  />
{:else if children}
  {@render children()}
{:else}
  <p class={cn("text-card-foreground font-consolas line-clamp-2 text-center text-xs break-all", className)}>
    {name}
  </p>
{/if}
