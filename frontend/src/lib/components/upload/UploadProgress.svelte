<script lang="ts">
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import LoaderCircle from "@lucide/svelte/icons/loader-circle";
  import X from "@lucide/svelte/icons/x";
  import { cn } from "$lib/utils";
  import Button from "../ui/button/button.svelte";
  import type { UploadState } from "./uploadState.svelte";

  let {
    uploadState,
    class: className,
  }: {
    uploadState: UploadState;
    class?: string;
  } = $props();
</script>

{#if uploadState.isVisible}
  <div class={cn("absolute right-4 bottom-4 w-80 rounded-lg border border-border bg-card shadow-lg", className)}>
    <!-- Header -->
    <div class="flex items-center justify-between border-b border-border px-4 py-3">
      <div class="flex items-center gap-2">
        {#if uploadState.allComplete}
          {#if uploadState.errorCount > 0}
            <CircleAlert class="h-4 w-4 text-destructive" />
          {:else}
            <CircleCheck class="h-4 w-4 text-green-500" />
          {/if}
        {:else}
          <LoaderCircle class="h-4 w-4 animate-spin text-primary" />
        {/if}
        <h3 class="text-sm font-semibold text-foreground">
          {#if uploadState.allComplete}
            Upload Complete
          {:else}
            Uploading Files
          {/if}
        </h3>
      </div>

      {#if uploadState.allComplete}
        <button
          onclick={() => uploadState.clearAll()}
          class="text-muted-foreground transition-colors hover:text-foreground"
          aria-label="Close"
        >
          <X class="h-4 w-4" />
        </button>
      {/if}
    </div>

    <!-- Progress Summary -->
    <div class="border-b border-border px-4 py-2">
      <div class="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {uploadState.successCount + uploadState.errorCount} / {uploadState.queue.length} files
        </span>
        {#if uploadState.errorCount > 0}
          <Button onclick={() => uploadState.retryFailed()} size="sm" class="h-auto text-xs">Retry</Button>
        {/if}
      </div>
      <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-secondary">
        <div
          class={cn("h-full transition-all duration-300", uploadState.errorCount > 0 ? "bg-destructive" : "bg-primary")}
          style="width: {((uploadState.successCount + uploadState.errorCount) / uploadState.queue.length) * 100}%"
        ></div>
      </div>
    </div>

    <!-- File List -->
    <div class="max-h-60 overflow-y-auto">
      {#each uploadState.queue as upload}
        <div class="flex items-center gap-3 border-b border-border px-4 py-2 last:border-b-0">
          <!-- Status Icon -->
          <div class="flex-shrink-0">
            {#if upload.status === "success"}
              <CircleCheck class="h-4 w-4 text-green-500" />
            {:else if upload.status === "error"}
              <CircleAlert class="h-4 w-4 text-destructive" />
            {:else if upload.status === "uploading"}
              <LoaderCircle class="h-4 w-4 animate-spin text-primary" />
            {:else}
              <div class="h-4 w-4 rounded-full bg-muted"></div>
            {/if}
          </div>

          <!-- File Info -->
          <div class="min-w-0 flex-1">
            <p class="truncate text-xs font-medium text-foreground">
              {upload.file.name}
            </p>
            {#if upload.status === "error" && upload.error}
              <p class="truncate text-xs text-destructive">
                {upload.error}
              </p>
            {:else if upload.status === "success"}
              <p class="text-xs text-muted-foreground">Uploaded successfully</p>
            {:else if upload.status === "uploading"}
              <p class="text-xs text-muted-foreground">Uploading...</p>
            {:else}
              <p class="text-xs text-muted-foreground">Waiting...</p>
            {/if}
          </div>

          <!-- File Size -->
          <div class="flex-shrink-0 text-xs text-muted-foreground">
            {(upload.file.size / 1024).toFixed(1)} KB
          </div>
        </div>
      {/each}
    </div>
  </div>
{/if}
