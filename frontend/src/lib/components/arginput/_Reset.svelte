<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";
  import { RotateCcw } from "@lucide/svelte";

  let {
    onReset,
    onmousedown,
    class: className,
  }: {
    onReset: () => void;
    onmousedown?: () => void;
    class?: string;
  } = $props();
</script>

<Button variant="ghost" size="icon" class={cn("h-6 w-6", className)} onclick={onReset} onmousedown={onmousedown} aria-label="Reset value">
  <RotateCcw class="text-muted-foreground size-3 opacity-50" />
</Button>