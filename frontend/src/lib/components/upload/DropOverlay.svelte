<script lang="ts">
  import type { Snippet } from "svelte";
  import Upload from "@lucide/svelte/icons/upload";
  import { cn } from "$lib/utils";

  let {
    class: className,
    children,
  }: {
    class?: string;
    children?: Snippet;
  } = $props();
</script>

<div
  role="status"
  aria-live="polite"
  class={cn(
    "pointer-events-none absolute inset-0 flex items-center justify-center border-2 border-dashed border-primary bg-primary/10 transition-all",
    className,
  )}
>
  <div class="rounded-lg bg-background p-6 shadow-lg">
    <Upload class="mx-auto mb-2 h-12 w-12 text-primary" />
    <p class="text-lg font-medium text-primary">
      {@render children?.()}
    </p>
  </div>
</div>
