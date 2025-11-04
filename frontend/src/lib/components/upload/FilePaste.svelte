<script lang="ts">
  import type { Snippet } from "svelte";
  import { extractFilesFromClipboard, filterFilesByAccept } from "./utils";

  let {
    mode = null,
    onFilesPaste,
    disabled = false,
    accept,
    children,
  }: {
    mode?: "global" | "focus" | null;
    onFilesPaste: (files: File[]) => void;
    disabled?: boolean;
    accept?: string;
    children?: Snippet;
  } = $props();

  /**
   * Process pasted files
   */
  function processFiles(event: ClipboardEvent): void {
    if (disabled) return;

    const items = event.clipboardData?.items;
    if (!items) return;

    // Extract files from clipboard
    let files = extractFilesFromClipboard(items);

    if (files.length === 0) return;

    // Prevent default paste behavior when files are detected
    event.preventDefault();

    // Filter files by accept attribute if provided
    if (accept) {
      files = filterFilesByAccept(files, accept);
    }
    if (files.length > 0) {
      onFilesPaste(files);
    }
  }

  /**
   * Handle global paste event
   */
  function handleGlobalPaste(event: ClipboardEvent): void {
    if (mode !== "global") return;
    processFiles(event);
  }
</script>

<!-- Global mode: listen to paste on entire window -->
<svelte:window onpaste={handleGlobalPaste} />

<!-- Focus mode: provide a focusable container -->
{#if mode === "focus"}
  <div
    tabindex="-1"
    role="region"
    aria-label="Paste file area - click here and press Ctrl/Cmd+V to paste files"
    onpaste={processFiles}
  >
    {@render children?.()}
  </div>
{:else}
  {@render children?.()}
{/if}
