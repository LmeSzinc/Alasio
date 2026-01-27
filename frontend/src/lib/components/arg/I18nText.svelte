<script lang="ts">
  import { cn } from "$lib/utils";

  let {
    text,
    class: className,
  }: {
    text: string | string[] | undefined | null;
    class?: string;
  } = $props();

  const lines = $derived.by(() => {
    if (!text) return [];
    if (typeof text === "string") return [text];
    return text;
  });
</script>

{#each lines as line}
  <p class={cn("wrap-break-word", className)}>{line}</p>
{/each}
