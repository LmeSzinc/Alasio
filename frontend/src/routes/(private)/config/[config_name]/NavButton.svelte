<script lang="ts">
  import SafeBold from "$lib/components/aside/SafeBold.svelte";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";

  let {
    name,
    active,
    scheduler = false,
    variant = "accordin",
    class: className,
    ...restprops
  }: {
    name: string;
    active: boolean;
    // show a gray outline circle on the right, marking a task that can be enabled in the scheduler
    scheduler?: boolean;
    onclick?: () => void;
    ondblclick?: () => void;
    variant?: "root" | "accordin";
    class?: string;
  } = $props();
</script>

<Button
  variant="ghost"
  class={cn(
    "hover:text-primary relative h-auto min-h-8 w-full justify-start px-3 py-1 text-left text-sm",
    active
      ? "text-primary hover:bg-card dark:hover:bg-card font-semibold"
      : "text-foreground/80 hover:bg-card/80 dark:hover:bg-card font-medium",
    variant === "root" && cn("text-md hover:bg-accent dark:hover:bg-accent hover:underline"),
    className,
  )}
  {...restprops}
>
  {#if active}
    <div class="bg-primary absolute top-0.5 bottom-0.5 left-0 w-1 rounded-r-full"></div>
  {/if}
  <SafeBold {active} text={name}></SafeBold>
  {#if scheduler}
    <!--
      Align the circle center with the accordion trigger chevron above:
      chevron (size-4) sits at trigger right padding px-3, rows are inset px-3,
      so place the circle at right-1 to share the chevron's horizontal center.
    -->
    <span
      class="border-muted-foreground/60 absolute top-1/2 right-1 h-2 w-2 -translate-y-1/2 rounded-full border"
      aria-hidden="true"
    ></span>
  {/if}
</Button>
