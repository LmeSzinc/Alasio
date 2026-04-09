<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";

  let {
    name,
    active,
    onclick,
    variant = "accordin",
    class: className,
  }: {
    name: string;
    active: boolean;
    onclick?: () => void;
    variant?: "root" | "accordin";
    class?: string;
  } = $props();
</script>

<Button
  variant="ghost"
  class={cn(
    "hover:text-primary relative h-8 w-full justify-start px-3 text-sm transition-colors",
    active
      ? "text-primary bg-card font-semibold shadow-xs hover:bg-card dark:hover:bg-card"
      : "text-foreground/80 hover:bg-card/80 dark:hover:bg-card font-medium",
    variant === "root" && cn("text-md hover:bg-accent shadow-none hover:underline dark:hover:bg-accent"),
    className,
  )}
  {onclick}
>
  {#if active}
    <div class="bg-primary absolute top-0.5 bottom-0.5 left-0 w-1 rounded-r-full"></div>
  {/if}
  {name}
</Button>
