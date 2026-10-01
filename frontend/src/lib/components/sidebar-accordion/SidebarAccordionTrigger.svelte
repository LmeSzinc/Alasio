<script lang="ts">
  import { Accordion as AccordionPrimitive } from "bits-ui";
  import { AccordionTrigger } from "$lib/components/ui/accordion";
  import { type WithoutChild, cn } from "$lib/utils.js";
  import SidebarRowIndicator from "./SidebarRowIndicator.svelte";
  import { sidebarRowClass } from "./row";

  // Sidebar variant of the accordion trigger: a row of the metrics every row
  // of the family uses, which replace the roomy padding of the base accordion
  // (py-4 on a trigger). The chevron keeps its base size-4 and sits at the
  // right padding of the metrics, callers that align something to it (see
  // NavButton) keep working.
  let {
    ref = $bindable(null),
    class: className,
    level = 3,
    children,
    ...restProps
  }: WithoutChild<AccordionPrimitive.TriggerProps> & {
    level?: AccordionPrimitive.HeaderProps["level"];
  } = $props();
</script>

<AccordionTrigger bind:ref {level} class={cn(sidebarRowClass, className)} {...restProps}>
  <!-- A group trigger is never the current row, it reserves the indicator so its label stays left aligned with the labels of the entries -->
  <SidebarRowIndicator />
  {@render children?.()}
</AccordionTrigger>
