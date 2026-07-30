<script lang="ts">
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { cn } from "$lib/utils";
  import { Hourglass } from "@lucide/svelte";
  import type { Snippet } from "svelte";

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
              "h-7 w-7 cursor-pointer rounded-full",
              "text-primary border-primary/60 flex items-center justify-center border-2",
              disabled ? "cursor-not-allowed opacity-50" : "hover:border-primary",
            )}
            {onclick}
            {disabled}
          >
            {#if children}
              {@render children()}
            {:else}
              <Hourglass class="h-3.5 w-3.5" />
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
