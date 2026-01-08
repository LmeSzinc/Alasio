<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { AlertCircle, Loader2 } from "@lucide/svelte";
  import { type Snippet } from "svelte";
  import type { HTMLImgAttributes } from "svelte/elements";

  type Props = {
    src: string | null | undefined;
    alt: string;
    class?: string;
    imageClass?: string;
    iconClass?: string;
    loading?: Snippet;
    error?: Snippet;
    // If image not loaded after given delay in ms, loading icon or snappet shows
    loadingDelay?: number;
    // To enable lazy loading, provide a rootMargin string.
    // e.g., "0px" to load when visible, "200px" to preload 200px before visible.
    // If null or undefined, lazy loading is disabled (eager loading).
    rootMargin?: string | null;
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
    rootMargin = null,
    ...rest
  }: Props = $props();

  let status: "loading" | "loaded" | "error" = $state("loading");
  let showLoader = $state(false);
  let loadingTimer: ReturnType<typeof setTimeout>;

  let containerEl: HTMLDivElement;
  let isIntersecting = $state(false);
  let imageSrc = $state<string | undefined | null>(undefined);

  $effect(() => {
    if (rootMargin === null) {
      isIntersecting = true;
      return;
    }

    if (!containerEl) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          isIntersecting = true;
          observer.unobserve(containerEl);
        }
      },
      { rootMargin }, // "0px", "200px", etc
    );

    observer.observe(containerEl);

    return () => {
      observer.disconnect();
    };
  });

  $effect(() => {
    if (!isIntersecting) return;

    if (!src) {
      status = "error";
      showLoader = false;
      imageSrc = null;
      return;
    }

    status = "loading";
    imageSrc = src;
    showLoader = false;

    clearTimeout(loadingTimer);
    // Show loading icon if still loading after 300ms
    loadingTimer = setTimeout(() => {
      if (status === "loading") {
        showLoader = true;
      }
    }, loadingDelay);

    return () => clearTimeout(loadingTimer);
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

<div
  bind:this={containerEl}
  class={cn(
    "relative flex h-full w-full items-center justify-center",
    status === "error" && "outline-muted-foreground outline-1 -outline-offset-1 outline-dashed",
    className,
  )}
>
  {#if status === "loading" && showLoader}
    <div class="absolute inset-0 flex items-center justify-center">
      {#if loading}
        {@render loading()}
      {:else}
        <Loader2 class={cn("text-primary h-1/2 w-1/2 animate-spin", iconClass)} />
      {/if}
    </div>
  {/if}

  {#if status === "error"}
    <div class="absolute inset-0 flex flex-col items-center justify-center">
      {#if error}
        {@render error()}
      {:else}
        <AlertCircle class={cn("text-muted-foreground h-1/2 w-1/2", iconClass)} />
      {/if}
    </div>
  {/if}

  {#if imageSrc}
    <img
      {...rest}
      src={imageSrc}
      {alt}
      class={cn(
        "h-full w-full object-contain transition-opacity duration-200",
        status === "loaded" ? "opacity-100" : "opacity-0",
        imageClass,
      )}
      {onload}
      {onerror}
      loading={rootMargin === null ? "eager" : "lazy"}
    />
  {/if}
</div>
