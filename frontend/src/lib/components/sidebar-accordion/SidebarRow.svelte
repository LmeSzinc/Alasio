<script lang="ts">
  import { Button, type ButtonProps } from "$lib/components/ui/button";
  import { cn } from "$lib/utils.js";
  import SidebarRowIndicator from "./SidebarRowIndicator.svelte";
  import { sidebarRowClass } from "./row";

  // A row of a sidebar: an entry that opens a page instead of a group
  // (Overview, Device, Config Manager, ...). It sits in the list of a sidebar
  // (see SidebarContent) or under the trigger of a group (see
  // SidebarAccordionContent); both stretch it and inset it through the margins
  // of the metrics.
  //
  // It is laid out like every row of the family: the metrics, the select
  // indicator (painted while the row is the current one) and the label. The
  // label is a child snippet, the caller decides whether the row text has to
  // keep the width of its bold form (see ConfigNav, which renders SafeBold).
  let {
    ref = $bindable(null),
    active = false,
    class: className,
    children,
    ...restProps
  }: ButtonProps & {
    active?: boolean;
  } = $props();
</script>

<Button
  bind:ref
  variant="ghost"
  class={cn(
    // h-auto: the default size of the button fixes h-9, a row grows with its
    // label; no w-full: the row is stretched by the list it sits in, so the
    // margins of the metrics inset it without overflowing
    "relative h-auto justify-start text-left",
    sidebarRowClass,
    "hover:bg-accent dark:hover:bg-accent hover:text-primary hover:underline",
    active ? "text-primary font-semibold" : "text-foreground font-medium",
    className,
  )}
  {...restProps}
>
  <SidebarRowIndicator {active} />
  {@render children?.()}
</Button>
