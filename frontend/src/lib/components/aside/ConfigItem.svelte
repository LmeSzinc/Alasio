<script lang="ts" module>
  import { tv, type VariantProps } from "tailwind-variants";

  export const badgeVariants = tv({
    base: "hover:bg-accent/50 focus:ring-ring flex w-full cursor-pointer flex-col items-center gap-1 mt-2 rounded-md transition-colors",
    variants: {
      variant: {
        default: "",
        active: "",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  });
  export type ItemVariant = VariantProps<typeof badgeVariants>["variant"];
</script>

<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { Play } from "@lucide/svelte";
  import type { ConfigLike } from "./types";

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    variant?: ItemVariant;
    class?: string;
    onclick?: (config: T) => void;
  };
  let { config, variant = "default", class: className, onclick }: Props = $props();

  // icon handing
  let iconError = $state(false);
  $effect(() => {
    // Reset iconError when config.mod changes
    iconError = false;
  });
  function handleIconError() {
    iconError = true;
  }

  // callbacks
  function handleClick() {
    onclick?.(config);
  }
</script>

<button
  class={cn(badgeVariants({ variant }), className)}
  onclick={handleClick}
  disabled={!onclick}
  aria-label="Open configuration: {config.name}"
  title={config.name}
>
  {#if config.mod && !iconError}
    <img
      src="/static/icon/{config.mod}.svg"
      alt=""
      role="presentation"
      class="h-8 w-8 object-contain"
      onerror={handleIconError}
    />
  {:else}
    <Play class="text-primary fill-primary h-8 w-8" aria-hidden="true" />
  {/if}
  <span class="text-muted-foreground line-clamp-2 text-center text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>
