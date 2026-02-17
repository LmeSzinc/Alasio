<script lang="ts">
  import { cn } from "$lib/utils.js";
  import ConfigState from "./ConfigState.svelte";
  import ModIcon from "./ModIcon.svelte";
  import { useWorkerState } from "./state.svelte";
  import type { ConfigLike, WORKER_STATE } from "./types";

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    state?: WORKER_STATE;
    active?: boolean;
    class?: string;
    onclick?: (config: T) => void;
    afspin?: boolean;
  };
  let { config, state = "idle", active = false, class: className, onclick, afspin = false }: Props = $props();

  const displayState = useWorkerState(() => state);
  const RUNNING_STATES: WORKER_STATE[] = ["running", "scheduler-stopping", "scheduler-waiting"];
  const spin = $derived(afspin && RUNNING_STATES.includes(displayState.value));

  // callbacks
  function handleClick() {
    onclick?.(config);
  }
</script>

<button
  class={cn(
    "hover:bg-accent relative flex w-16 cursor-pointer flex-col items-center rounded-md py-1.5 transition-colors",
    active ? "text-primary" : "hover:text-primary text-foreground/85",
    className,
  )}
  onclick={handleClick}
  disabled={!onclick}
  aria-label="Open configuration: {config.name}"
  title={config.name}
>
  {#if active}
    <div class="bg-primary absolute top-2 bottom-2 left-0 w-1 rounded-r-full"></div>
  {/if}
  <div class="relative">
    <ModIcon mod={config.mod} afspin={spin} />
    <ConfigState state={displayState.value} {active} class="absolute -right-1 bottom-0" />
  </div>
  <span class="line-clamp-2 text-center text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>
