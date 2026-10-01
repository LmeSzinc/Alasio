<script lang="ts">
  import { Accordion as AccordionPrimitive } from "bits-ui";
  import { Accordion } from "$lib/components/ui/accordion";
  import { cn } from "$lib/utils.js";

  /**
   * Sidebar variant of $lib/components/ui/accordion, for the nav sidebars of
   * the app.
   *
   * A nav sidebar stacks many collapsed rows in a narrow column, so the parts
   * trade the generous padding of the base accordion for the compact metrics
   * of a sidebar row. The root only pins the text size that the parts and any
   * custom content inside a group inherit.
   *
   * The API is the one of the base accordion: the defaults of a part are
   * merged with the class a caller passes, so a caller overrides them the same
   * way (cn keeps the caller's class).
   */
  let {
    ref = $bindable(null),
    value = $bindable(),
    class: className,
    ...restProps
  }: AccordionPrimitive.RootProps = $props();
</script>

<Accordion bind:ref bind:value={value as never} class={cn("text-sm", className)} {...restProps} />
