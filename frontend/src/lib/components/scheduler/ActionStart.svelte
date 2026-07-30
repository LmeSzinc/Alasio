<script lang="ts">
  import * as Tooltip from "$lib/components/ui/tooltip";
  import { cn } from "$lib/utils";
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

<div class={cn("w-full", className)}>
  <Tooltip.Provider>
    <Tooltip.Root {disabled}>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <button
            {...props}
            class={cn(
              "h-7 w-full cursor-pointer rounded-full",
              "bg-primary text-primary-foreground text-sm font-semibold",
              disabled ? "cursor-not-allowed opacity-50" : "hover:opacity-80",
            )}
            {onclick}
            {disabled}
          >
            {#if children}
              {@render children?.()}
            {:else}
              {title}
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
