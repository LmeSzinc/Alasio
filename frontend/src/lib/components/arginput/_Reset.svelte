<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";
  import { RotateCcw } from "@lucide/svelte";

  let {
    onReset,
    class: className,
  }: {
    onReset: () => void;
    class?: string;
  } = $props();
</script>

<Button variant="ghost" size="icon" class={cn("h-6 w-6", className)} onclick={onReset} aria-label="Reset value">
  <RotateCcw class="text-muted-foreground size-3 opacity-50" />
</Button>
