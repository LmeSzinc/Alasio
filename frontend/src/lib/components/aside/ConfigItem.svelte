<script lang="ts">
  import { cn } from "$lib/utils.js";
  import { Play } from "@lucide/svelte";
  import ConfigStatus from "./ConfigStatus.svelte";
  import type { ConfigLike, WORKER_STATUS } from "./types";

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    status?: WORKER_STATUS;
    active?: boolean;
    class?: string;
    onclick?: (config: T) => void;
    afspin?: boolean;
  };
  let { config, status = "idle", active = false, class: className, onclick, afspin = false }: Props = $props();

  // Easter egg spinning
  // afspin should be True on April 1st
  const RUNNING_STATUSES: WORKER_STATUS[] = ["running", "scheduler-stopping", "scheduler-waiting"];
  const spin = $derived(afspin && RUNNING_STATUSES.includes(status));

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
  class={cn(
    "focus:ring-ring flex w-16 cursor-pointer flex-col items-center rounded-md py-1.5 transition-colors",
    active
      ? "bg-primary hover:bg-primary text-primary-foreground/85"
      : "hover:bg-accent hover:text-primary text-foreground/70",
    className,
  )}
  onclick={handleClick}
  disabled={!onclick}
  aria-label="Open configuration: {config.name}"
  title={config.name}
>
  <div class="relative">
    {#if config.mod && !iconError}
      <img
        src="/static/icon/{config.mod}.svg"
        alt=""
        role="presentation"
        class={cn("h-8 w-8 object-contain", spin && "origin-[50%_42%] animate-[spin_400ms_linear_infinite]")}
        onerror={handleIconError}
      />
    {:else}
      <Play class={cn("h-8 w-8", spin && "origin-[50%_42%] animate-[spin_400ms_linear_infinite]")} strokeWidth="1.5" aria-hidden="true" />
    {/if}
    <ConfigStatus {status} {active} class="pointer-events-none absolute -right-1 bottom-0" />
  </div>
  <span class="line-clamp-2 text-center text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>
