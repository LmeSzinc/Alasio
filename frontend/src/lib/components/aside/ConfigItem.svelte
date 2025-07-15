<script lang="ts">
  import { Play } from "@lucide/svelte";

  // Generic type that only requires name and mod properties
  export type ConfigLike = {
    name: string;
    mod: string;
    [key: string]: any; // Allow any other properties
  };

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    onclick?: (config: T) => void;
  };
  let { config, onclick }: Props = $props();

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
  class="hover:bg-accent/50 focus:ring-ring flex w-full cursor-pointer flex-col items-center gap-1 rounded-md transition-colors focus:ring-2 focus:ring-offset-2 focus:outline-none"
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
      class="h-6 w-6 object-contain"
      onerror={handleIconError}
    />
  {:else}
    <Play class="text-primary fill-primary h-6 w-6" aria-hidden="true" />
  {/if}
  <span class="text-muted-foreground line-clamp-2 text-center font-mono text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>
