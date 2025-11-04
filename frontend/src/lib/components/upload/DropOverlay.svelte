<script lang="ts">
  import { Upload } from "@lucide/svelte";
  import { cn } from "$lib/utils";
  import type { Snippet } from "svelte";

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
    "bg-primary/10 border-primary pointer-events-none absolute inset-0 flex items-center justify-center border-2 border-dashed transition-all",
    className,
  )}
>
  <div class="bg-background rounded-lg p-6 shadow-lg">
    <Upload class="text-primary mx-auto mb-2 h-12 w-12" />
    <p class="text-primary text-lg font-medium">
      {@render children?.()}
    </p>
  </div>
</div>
