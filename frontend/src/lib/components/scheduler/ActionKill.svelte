<script lang="ts">
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { Ghost } from "@lucide/svelte";
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
    "h-7 w-7 cursor-pointer rounded-full",
    "text-destructive border-destructive/60 flex items-center justify-center border-2",
    disabled ? "cursor-not-allowed opacity-50" : "",
    className,
  )}
  {onclick}
  {disabled}
  aria-disabled={disabled}
  title={title || t.Scheduler.Kill()}
>
  {#if children}
    {@render children()}
  {:else}
    <Ghost class="h-3.5 w-3.5" strokeWidth="2" />
  {/if}
</button>
