<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import DropOverlay from "./DropOverlay.svelte";
  import FilePaste from "./FilePaste.svelte";
  import UploadProgress from "./UploadProgress.svelte";
  import { UploadState, type OnUploadFunction } from "./uploadState.svelte";
  import { filterFilesByAccept } from "./utils";

  let {
    onUpload,
    disabled = false,
    accept,
    paste = null,
    overlayText = "Drop files to upload",
    class: className,
    children,
    overlay,
  }: {
    onUpload: OnUploadFunction;
    disabled?: boolean;
    accept?: string;
    paste?: "global" | "focus" | null;
    overlayText?: string;
    class?: string;
    children: Snippet;
    overlay?: Snippet;
  } = $props();

  // Create upload state instance
  const uploadState = new UploadState(onUpload);

  // File input reference (no $state needed for bind:ref)
  let fileInput: HTMLInputElement;

  let isDragging = $state(false);
  let dragCounter = $state(0);

  /**
   * Handle file input change
   */
  function handleFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      uploadState.addFiles(input.files);
      // Reset input so the same file can be selected again
      input.value = "";
    }
  }

  /**
   * Handle pasted files
   */
  function handleFilesPaste(files: File[]): void {
    uploadState.addFiles(files);
  }

  /**
   * Open file picker dialog
   */
  export function openFilePicker(): void {
    fileInput?.click();
  }

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
          uploadState.addFiles(acceptedFiles);
        }
      } else {
        uploadState.addFiles(files);
      }
    }
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
  <!-- Paste file handler -->
  <FilePaste {disabled} {accept} mode={paste} onFilesPaste={handleFilesPaste}>
    <!-- Main content -->
    {@render children()}
  </FilePaste>

  <!-- Drag overlay -->
  {#if isDragging && !disabled}
    {#if overlay}
      {@render overlay()}
    {:else}
      <DropOverlay>{overlayText}</DropOverlay>
    {/if}
  {/if}

  <!-- Upload progress component -->
  <UploadProgress {uploadState} />

  <!-- Hidden file input -->
  <input bind:this={fileInput} type="file" multiple {accept} class="hidden" onchange={handleFileChange} />
</div>
