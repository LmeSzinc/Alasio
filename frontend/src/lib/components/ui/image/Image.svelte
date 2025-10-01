<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { AlertCircle, Loader2 } from "@lucide/svelte";
  import { onDestroy, type Snippet } from "svelte";
  import type { HTMLImgAttributes } from "svelte/elements";

  type Props = {
    src: string | null | undefined;
    alt: string;
    // Additional class adding to div
    class?: string;
    // Additional class adding to img
    imageClass?: string;
    // Additional class adding to default icons
    iconClass?: string;
    loading?: Snippet;
    error?: Snippet;
    // If image not loaded after given delay in ms, loading icon or snappet shows
    loadingDelay?: number;
  } & HTMLImgAttributes;

  let {
    src,
    alt,
    class: className,
    imageClass,
    iconClass,
    loading,
    error,
    loadingDelay = 300,
    ...rest
  }: Props = $props();

  let status: "loading" | "loaded" | "error" = $state("loading");
  let showLoader = $state(false);
  let loadingTimer: number | undefined;
  onDestroy(() => {
    clearInterval(loadingTimer);
  });
  $effect(() => {
    if (!src) {
      status = "error";
      showLoader = false;
      clearTimeout(loadingTimer);
      return;
    }
    status = "loading";
    showLoader = false;
    clearTimeout(loadingTimer);
    // Show loading icon if still loading after 300ms
    loadingTimer = setTimeout(() => {
      if (status === "loading") {
        showLoader = true;
      }
    }, loadingDelay);
  });
  function onload() {
    status = "loaded";
    showLoader = false;
    clearTimeout(loadingTimer);
  }
  function onerror() {
    status = "error";
    showLoader = false;
    clearTimeout(loadingTimer);
  }
</script>

<div class={cn("relative flex h-full w-full items-center justify-center", className)}>
  {#if status === "loading"}
    <div class="absolute inset-0 flex items-center justify-center">
      {#if loading}
        {@render loading()}
      {:else}
        <Loader2 class={cn("text-primary h-1/3 w-1/3 animate-spin", iconClass)} />
      {/if}
    </div>
  {/if}

  {#if status === "error"}
    <div class="absolute inset-0 flex flex-col items-center justify-center">
      {#if error}
        {@render error()}
      {:else}
        <AlertCircle class={cn("text-muted-foreground h-1/3 w-1/3", iconClass)} />
      {/if}
    </div>
  {/if}

  {#if src}
    <img
      {...rest}
      {src}
      {alt}
      class={cn(
        "h-full w-full object-contain transition-opacity duration-200",
        status === "loaded" ? "opacity-100" : "opacity-0",
        imageClass,
      )}
      {onload}
      {onerror}
    />
  {/if}
</div>
