<script lang="ts">
  import { Upload } from "@lucide/svelte";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  let {
    onDrop,
    disabled = false,
    accept,
    showOverlay = true,
    overlayText = "Drop files to upload",
    class: className,
    children,
  }: {
    onDrop?: (files: FileList) => void;
    disabled?: boolean;
    accept?: string;
    showOverlay?: boolean;
    overlayText?: string;
    class?: string;
    children: Snippet;
  } = $props();

  let isDragging = $state(false);
  let dragCounter = $state(0);

  /**
   * Handle drag over event
   */
  function handleDragOver(event: DragEvent): void {
    if (disabled) return;

    event.preventDefault();
    event.stopPropagation();

    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
  }

  /**
   * Handle drag enter event
   * Use counter to handle nested elements properly
   */
  function handleDragEnter(event: DragEvent): void {
    if (disabled) return;

    event.preventDefault();
    event.stopPropagation();

    dragCounter++;

    if (event.dataTransfer?.items) {
      // Check if dragged items contain files
      const hasFiles = Array.from(event.dataTransfer.items).some((item) => item.kind === "file");

      if (hasFiles) {
        isDragging = true;
      }
    } else {
      // Fallback for browsers that don't support items
      isDragging = true;
    }
  }

  /**
   * Handle drag leave event
   */
  function handleDragLeave(event: DragEvent): void {
    if (disabled) return;

    event.preventDefault();
    event.stopPropagation();

    dragCounter--;

    // Only set isDragging to false when leaving the entire drop zone
    if (dragCounter === 0) {
      isDragging = false;
    }
  }

  /**
   * Handle drop event
   */
  function handleDrop(event: DragEvent): void {
    if (disabled) return;

    event.preventDefault();
    event.stopPropagation();

    isDragging = false;
    dragCounter = 0;

    const files = event.dataTransfer?.files;

    if (files && files.length > 0) {
      // Filter files by accept attribute if provided
      if (accept) {
        const acceptedFiles = filterFilesByAccept(files, accept);
        if (acceptedFiles.length > 0) {
          // Create a new FileList-like object
          const dataTransfer = new DataTransfer();
          acceptedFiles.forEach((file) => dataTransfer.items.add(file));
          onDrop?.(dataTransfer.files);
        }
      } else {
        onDrop?.(files);
      }
    }
  }

  /**
   * Filter files based on accept attribute
   */
  function filterFilesByAccept(files: FileList, accept: string): File[] {
    const acceptTypes = accept.split(",").map((type) => type.trim());

    return Array.from(files).filter((file) => {
      return acceptTypes.some((acceptType) => {
        // Handle MIME types (e.g., "image/*", "image/png")
        if (acceptType.includes("/")) {
          if (acceptType.endsWith("/*")) {
            const category = acceptType.split("/")[0];
            return file.type.startsWith(category + "/");
          }
          return file.type === acceptType;
        }

        // Handle file extensions (e.g., ".png", ".jpg")
        if (acceptType.startsWith(".")) {
          return file.name.toLowerCase().endsWith(acceptType.toLowerCase());
        }

        return false;
      });
    });
  }
</script>

<div
  role="group"
  aria-label={disabled ? "File drop area (disabled)" : "File drop area - drag and drop files here"}
  aria-disabled={disabled}
  class={cn("relative h-full w-full transition-all", className)}
  ondragover={handleDragOver}
  ondragenter={handleDragEnter}
  ondragleave={handleDragLeave}
  ondrop={handleDrop}
>
  <!-- Main content -->
  {@render children()}

  <!-- Drag overlay -->
  {#if showOverlay && isDragging && !disabled}
    <div
      role="status"
      aria-live="polite"
      class="bg-primary/10 border-primary pointer-events-none absolute inset-0 flex items-center justify-center border-2 border-dashed transition-all"
    >
      <div class="bg-background rounded-lg p-6 shadow-lg">
        <Upload class="text-primary mx-auto mb-2 h-12 w-12" />
        <p class="text-primary text-lg font-medium">{overlayText}</p>
      </div>
    </div>
  {/if}
</div>
