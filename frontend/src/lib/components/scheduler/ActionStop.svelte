<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

  let {
    children,
    onclick,
    disabled,
    title,
    class: className,
  }: {
    children?: Snippet;
    onclick?: (e: Event) => void;
    disabled?: boolean;
    title?: string;
    class?: string;
  } = $props();
</script>

<button
  class={cn(
    "h-7 w-full cursor-pointer rounded-full",
    "text-primary border-primary/75 border-2 text-sm font-semibold",
    disabled ? "cursor-not-allowed opacity-50" : "",
    className,
  )}
  {onclick}
  {disabled}
  aria-disabled={disabled}
  {title}
>
  {#if children}
    {@render children?.()}
  {:else}
    {title}
  {/if}
</button>
