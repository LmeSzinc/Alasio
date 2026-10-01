<script lang="ts">
  import SafeBold from "$lib/components/aside/SafeBold.svelte";
  import { SidebarRowIndicator, sidebarRowClass } from "$lib/components/sidebar-accordion";
  import { Button } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";

  let {
    name,
    active,
    scheduler,
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
    class?: string;
  } = $props();
</script>

<Button
  variant="ghost"
  class={cn(
    // The box of a row of the sidebar accordion family: the metrics of the
    // family plus the state colors of a row of the nav. The label uses the
    // same metrics, so a card lines up with the entries and the groups above
    // it (see sidebar-accordion/row.ts). No w-full: the row is stretched by
    // the list it sits in, so the margins of the metrics inset it.
    "hover:text-primary h-auto justify-start text-left",
    sidebarRowClass,
    active
      ? "text-primary hover:bg-card dark:hover:bg-card font-semibold"
      : "text-foreground/80 hover:bg-card/80 dark:hover:bg-card font-medium",
    className,
  )}
  {...restprops}
>
  <SidebarRowIndicator {active} />
  <SafeBold {active} text={name}></SafeBold>
  {#if scheduler !== undefined}
    <!--
      The scheduler state of the card, a dot at the right end of the row. This
      row and the trigger of the group above it share the metrics of
      sidebar-accordion/row.ts: the chevron (size-4) of the trigger ends at the
      right padding of a row, the dot (size-2.5) is laid out in the row and
      held mr-[3px] (half a chevron minus half a dot) off the content edge, so
      the two share a horizontal center whatever padding the metrics carry.
      Enabled state is three layers: inner dot (theme color), gap, gray ring.
      The inner dot is painted by the background of this same box, a child
      element of this size has its own box on fractional device pixels
      (0.5px at 125% display scale) and gets pixel-snapped separately, which
      makes the dot land off-center inside the ring.
    -->
    <span
      class={cn(
        "border-muted-foreground/60 mr-[3px] h-2.5 w-2.5 shrink-0 rounded-full border",
        // filled disc of 2px radius, the remaining 1px inside the border is the gap
        scheduler && "border-primary bg-[radial-gradient(circle,var(--primary)_0_2.5px,transparent_2.5px)]",
      )}
      aria-hidden="true"
    ></span>
  {/if}
</Button>
