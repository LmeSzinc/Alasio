<script lang="ts">
  import { cn } from "$lib/utils";
  import { CircleAlert, CircleCheck, LoaderCircle, X } from "@lucide/svelte";
  import type { UploadState } from "./uploadState.svelte";
  import Button from "../ui/button/button.svelte";

  let {
    uploadState,
    class: className,
  }: {
    uploadState: UploadState;
    class?: string;
  } = $props();
</script>

{#if uploadState.isVisible}
  <div class={cn("bg-card border-border absolute right-4 bottom-4 w-80 rounded-lg border shadow-lg", className)}>
    <!-- Header -->
    <div class="border-border flex items-center justify-between border-b px-4 py-3">
      <div class="flex items-center gap-2">
        {#if uploadState.allComplete}
          {#if uploadState.errorCount > 0}
            <CircleAlert class="text-destructive h-4 w-4" />
          {:else}
            <CircleCheck class="h-4 w-4 text-green-500" />
          {/if}
        {:else}
          <LoaderCircle class="text-primary h-4 w-4 animate-spin" />
        {/if}
        <h3 class="text-foreground text-sm font-semibold">
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
          class="text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Close"
        >
          <X class="h-4 w-4" />
        </button>
      {/if}
    </div>

    <!-- Progress Summary -->
    <div class="border-border border-b px-4 py-2">
      <div class="text-muted-foreground flex items-center justify-between text-xs">
        <span>
          {uploadState.successCount + uploadState.errorCount} / {uploadState.queue.length} files
        </span>
        {#if uploadState.errorCount > 0}
          <Button onclick={() => uploadState.retryFailed()} size="sm" class="h-auto text-xs">Retry</Button>
        {/if}
      </div>
      <div class="bg-secondary mt-2 h-1.5 w-full overflow-hidden rounded-full">
        <div
          class={cn("h-full transition-all duration-300", uploadState.errorCount > 0 ? "bg-destructive" : "bg-primary")}
          style="width: {((uploadState.successCount + uploadState.errorCount) / uploadState.queue.length) * 100}%"
        ></div>
      </div>
    </div>

    <!-- File List -->
    <div class="max-h-60 overflow-y-auto">
      {#each uploadState.queue as upload}
        <div class="border-border flex items-center gap-3 border-b px-4 py-2 last:border-b-0">
          <!-- Status Icon -->
          <div class="flex-shrink-0">
            {#if upload.status === "success"}
              <CircleCheck class="h-4 w-4 text-green-500" />
            {:else if upload.status === "error"}
              <CircleAlert class="text-destructive h-4 w-4" />
            {:else if upload.status === "uploading"}
              <LoaderCircle class="text-primary h-4 w-4 animate-spin" />
            {:else}
              <div class="bg-muted h-4 w-4 rounded-full"></div>
            {/if}
          </div>

          <!-- File Info -->
          <div class="min-w-0 flex-1">
            <p class="text-foreground truncate text-xs font-medium">
              {upload.file.name}
            </p>
            {#if upload.status === "error" && upload.error}
              <p class="text-destructive truncate text-xs">
                {upload.error}
              </p>
            {:else if upload.status === "success"}
              <p class="text-muted-foreground text-xs">Uploaded successfully</p>
            {:else if upload.status === "uploading"}
              <p class="text-muted-foreground text-xs">Uploading...</p>
            {:else}
              <p class="text-muted-foreground text-xs">Waiting...</p>
            {/if}
          </div>

          <!-- File Size -->
          <div class="text-muted-foreground flex-shrink-0 text-xs">
            {(upload.file.size / 1024).toFixed(1)} KB
          </div>
        </div>
      {/each}
    </div>
  </div>
{/if}
