<script lang="ts">
  import type { Snippet } from "svelte";
  import CirclePlay from "@lucide/svelte/icons/circle-play";
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
              <!-- The circle of the icon is the button itself, no frame of its own;
                   stroke 1 (not 2): at 28px the default would be a 2.3px ring -->
              <!-- h-7 button and svg box; scale-[1.2] grows the lucide circle
                   (20/24 of the box) to the same 28px as the outlined pill -->
              <CirclePlay class="h-7 w-7 shrink-0 scale-[1.2] group-hover:[stroke-width:1.5]" strokeWidth="1" />
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
