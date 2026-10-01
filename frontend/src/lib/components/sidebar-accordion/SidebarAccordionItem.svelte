<script lang="ts">
  import { Accordion as AccordionPrimitive } from "bits-ui";
  import { AccordionItem } from "$lib/components/ui/accordion";
  import { cn } from "$lib/utils.js";

  // Sidebar variant of the accordion item: a nav group is delimited by the
  // background of its content, not by a separator line between the groups.
  let { ref = $bindable(null), class: className, ...restProps }: AccordionPrimitive.ItemProps = $props();
</script>

<!--
  The base item draws `not-last:border-b`. A plain border utility cannot cancel
  it: a variant class outranks a plain one whatever the order they are merged
  in, so the override carries the important modifier.
-->
<AccordionItem bind:ref class={cn("border-none!", className)} {...restProps} />
