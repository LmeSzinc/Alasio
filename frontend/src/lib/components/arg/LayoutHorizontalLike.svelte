<script lang="ts">
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";
  import I18nText from "./I18nText.svelte";
  import { getArgName, type ArgData } from "./utils.svelte";

  let {
    children,
    data,
    InputSnippet,
    PlaceholderSnippet,
    class: className,
  }: {
    children?: Snippet;
    data: ArgData;
    InputSnippet?: Snippet;
    PlaceholderSnippet?: Snippet;
    class?: string;
  } = $props();

  const displayName = $derived(getArgName(data));
</script>

<div class={cn("flex flex-col justify-center gap-y-1", className)}>
  <!-- Top row: name/children (left) and InputSnippet (right) -->
  <div class="flex min-h-7 flex-row items-center justify-between gap-x-4">
    <!-- Top-left: name + children -->
    <div class="flex min-w-0 flex-1 flex-row items-center gap-x-1 overflow-hidden">
      <I18nText text={displayName} class="font-medium" />
      {#if children}
        {@render children()}
      {/if}
    </div>

    <!-- Top-right: InputSnippet -->
    {#if InputSnippet}
      <div class="flex w-9/20 max-w-50 justify-center">
        {@render InputSnippet()}
      </div>
    {/if}
  </div>

  <!-- Bottom row: help text (left) and PlaceholderSnippet (right) -->
  {#if data.help || PlaceholderSnippet}
    <div class="flex flex-row gap-x-4">
      <!-- Bottom-left: help text -->
      <div class={cn("flex min-w-0 flex-col justify-center gap-0.5", PlaceholderSnippet ? "flex-1" : "")}>
        {#if data.help}
          <I18nText text={data.help} class="text-muted-foreground text-xs" />
        {/if}
      </div>

      <!-- Bottom-right: PlaceholderSnippet -->
      {#if PlaceholderSnippet}
        <div class="w-9/20 max-w-50">
          {@render PlaceholderSnippet()}
        </div>
      {/if}
    </div>
  {/if}
</div>
