<script lang="ts" module>
  import { cn, type WithElementRef } from "$lib/utils.js";
  import type { HTMLAttributes } from "svelte/elements";
  import { tv, type VariantProps } from "tailwind-variants";

  export const indicatorVariants = tv({
    base: [
      "absolute z-10 bg-primary pointer-events-none",
      "before:absolute before:box-border before:h-[8px] before:w-[8px] before:rounded-full before:border-2 before:border-primary before:content-['']",
    ],
    variants: {
      edge: {
        top: ["left-0 right-0 h-[2px] top-0 -translate-y-1/2", "before:left-0 before:top-1/2 before:-translate-y-1/2"],
        bottom: [
          "left-0 right-0 h-[2px] bottom-0 translate-y-1/2",
          "before:left-0 before:top-1/2 before:-translate-y-1/2",
        ],
        left: [
          "top-0 bottom-0 w-[2px] left-0 -translate-x-1/2",
          "before:top-0 before:left-1/2 before:-translate-x-1/2",
        ],
        right: [
          "top-0 bottom-0 w-[2px] right-0 translate-x-1/2",
          "before:top-0 before:left-1/2 before:-translate-x-1/2",
        ],
      },
    },
  });
  export type IndicatorEdge = VariantProps<typeof indicatorVariants>["edge"];
</script>

<script lang="ts">
  let {
    ref = $bindable(null),
    class: className,
    edge = null,
    ...restProps
  }: WithElementRef<HTMLAttributes<HTMLDivElement>> & {
    edge?: IndicatorEdge | null;
  } = $props();
</script>

{#if edge}
  <div bind:this={ref} data-slot="indicator" class={cn(indicatorVariants({ edge }), className)} {...restProps}></div>
{/if}
