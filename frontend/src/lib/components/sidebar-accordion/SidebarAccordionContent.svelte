<script lang="ts">
  import { Accordion as AccordionPrimitive } from "bits-ui";
  import { AccordionContent } from "$lib/components/ui/accordion";
  import { type WithoutChild, cn } from "$lib/utils.js";

  // Sidebar variant of the accordion content: the rows of a nav group are
  // already compact, so the block around them only keeps a small vertical
  // padding (py-2 instead of the pb-4 of the base accordion). The horizontal
  // padding stays in the rows, they carry the metrics of the family like every
  // other row of the nav.
  let {
    ref = $bindable(null),
    class: className,
    children,
    ...restProps
  }: WithoutChild<AccordionPrimitive.ContentProps> = $props();
</script>

<AccordionContent bind:ref {children} class={cn("py-2", className)} {...restProps} />
