<script lang="ts">
  import type { Snippet } from "svelte";
  import CirclePause from "@lucide/svelte/icons/circle-pause";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { cn } from "$lib/utils";

  let {
    children,
    onclick,
    disabled,
    title,
    class: className,
  }: {
    children?: Snippet;
    onclick?: (e: Event) => void;
    disabled?: boolean;
    title: string;
    class?: string;
  } = $props();
</script>

<div class={className}>
  <Tooltip.Provider>
    <Tooltip.Root {disabled}>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <button
            {...props}
            class={cn(
              "text-primary flex h-7 w-7 cursor-pointer items-center justify-center",
              disabled ? "cursor-not-allowed opacity-50" : "group",
            )}
            {onclick}
            {disabled}
          >
            {#if children}
              {@render children()}
            {:else}
              <!-- The circle of the icon is the button itself, no frame of its own.
                   The button is h-7 (28px) like the outlined pill; the svg box is
                   h-7 too and scale-[1.2] grows the lucide circle (20/24 of the
                   box) to the same 28px, so the ring matches the pill height.
                   Hover thickens the stroke like the stop pill thickens its
                   border (the group is on the button, a disabled one has none). -->
              <CirclePause class="h-7 w-7 shrink-0 scale-[1.2] group-hover:[stroke-width:1.5]" strokeWidth="1" />
            {/if}
          </button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>
        {title}
      </Tooltip.Content>
    </Tooltip.Root>
  </Tooltip.Provider>
</div>
