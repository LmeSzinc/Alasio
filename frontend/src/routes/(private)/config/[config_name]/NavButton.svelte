<script lang="ts">
  import SafeBold from "$lib/components/aside/SafeBold.svelte";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";

  let {
    name,
    active,
    scheduler,
    variant = "accordin",
    class: className,
    ...restprops
  }: {
    name: string;
    active: boolean;
    // Dot on the right, marking a task that can be enabled in the scheduler:
    // - true: the task is enabled, a gray outline circle with an inner dot in
    //   the theme color
    // - false: the task is disabled, a gray outline circle only
    // - undefined: not a task, or the state is not known, no circle
    scheduler?: boolean | undefined;
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
  {#if scheduler !== undefined}
    <!--
      Align the circle center with the accordion trigger chevron above:
      chevron (size-4) sits at trigger right padding px-3, rows are inset px-3,
      so place the circle at right-1 to share the chevron's horizontal center.
      Enabled state is three layers: inner dot (theme color), gap, gray ring.
      The inner dot is painted by the background of this same box, a child
      element of this size has its own box on fractional device pixels
      (0.5px at 125% display scale) and gets pixel-snapped separately, which
      makes the dot land off-center inside the ring.
    -->
    <span
      class={cn(
        "border-muted-foreground/60 absolute top-1/2 right-1 h-2.5 w-2.5 -translate-y-1/2 rounded-full border",
        // filled disc of 2px radius, the remaining 1px inside the border is the gap
        scheduler && "bg-[radial-gradient(circle,var(--primary)_0_2.5px,transparent_2.5px)] border-primary",
      )}
      aria-hidden="true"
    ></span>
  {/if}
</Button>
