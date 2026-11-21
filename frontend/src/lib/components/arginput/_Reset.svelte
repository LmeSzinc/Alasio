<script lang="ts">
  import RotateCcw from "@lucide/svelte/icons/rotate-ccw";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils";

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

<Button
  variant="ghost"
  size="icon"
  class={cn("h-6 w-6", className)}
  onclick={onReset}
  {onmousedown}
  aria-label="Reset value"
>
  <RotateCcw class="size-3 text-muted-foreground opacity-50" />
</Button>
