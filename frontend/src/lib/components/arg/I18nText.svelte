<script lang="ts">
  import { cn } from "$lib/utils";
  import { type I18nTextData, i18nTextToLines } from "./utils.svelte";

  let {
    text,
    class: className,
  }: {
    text: I18nTextData | undefined | null;
    class?: string;
  } = $props();

  const lines = $derived(i18nTextToLines(text));
</script>

<!--
  One block per text, so the lines stack whatever the parent lays out. A caller
  often puts the text in a flex row next to a button or an input: rendering the
  lines as siblings would lay them out side by side there.
-->
{#if lines.length}
  <div class={cn("flex flex-col", className)}>
    {#each lines as line}
      <p class="wrap-break-word">{line}</p>
    {/each}
  </div>
{/if}
