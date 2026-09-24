<script lang="ts">
  import type { Snippet } from "svelte";
  import { cn } from "$lib/utils";

  // The button shows its own name as its label, so it carries no tooltip: one
  // repeating the visible text is noise. The icon-only actions
  // (ActionSchedulerStop / ActionSchedulerContinue / ActionCancelResume) keep
  // theirs, there the tooltip is the only place the name shows.

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
    title: string;
    class?: string;
  } = $props();
</script>

<div class={cn("w-full", className)}>
  <button
    type="button"
    class={cn(
      "h-7 w-full cursor-pointer rounded-full",
      "bg-primary text-primary-foreground text-sm font-semibold",
      disabled ? "cursor-not-allowed opacity-50" : "hover:opacity-80",
    )}
    {onclick}
    {disabled}
  >
    {#if children}
      {@render children?.()}
    {:else}
      {title}
    {/if}
  </button>
</div>
