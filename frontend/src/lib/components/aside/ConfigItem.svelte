<script lang="ts">
  import { cn } from "$lib/utils.js";
  import ConfigState from "./ConfigState.svelte";
  import ModIcon from "./ModIcon.svelte";
  import { useWorkerState } from "./state.svelte";
  import type { ConfigLike, RestartTopicLike, WORKER_STATE } from "./types";

  // props
  type Props<T extends ConfigLike = ConfigLike> = {
    config: T;
    state?: WORKER_STATE;
    /**
     * Flow owning the backend (the Restart topic): the startup flow applies the
     * first full update of the backend (check, download, apply) before any
     * config starts, and the item marks that update in its top right corner
     */
    flow?: RestartTopicLike["flow"];
    active?: boolean;
    class?: string;
    onclick?: (config: T) => void;
    afspin?: boolean;
  };
  let {
    config,
    state = "idle",
    flow = "",
    active = false,
    class: className,
    onclick,
    afspin = false,
  }: Props = $props();

  const displayState = useWorkerState(() => state);
  // The update of the process start gets a badge of its own in the top right
  // corner, so it never hides the worker state in the bottom right one. A
  // config the backend itself reports as "updating" already carries that
  // badge, and the marker would only duplicate it. The test is made on the
  // state the item is given, not on the animated display value: the marker
  // follows the flow at once, without waiting for the state badge animation.
  const startupUpdate = $derived(flow === "startup" && state !== "updating");
  const RUNNING_STATES: WORKER_STATE[] = ["running", "scheduler-stopping", "scheduler-waiting"];
  const spin = $derived(afspin && RUNNING_STATES.includes(displayState.value));

  // callbacks
  function handleClick() {
    onclick?.(config);
  }
</script>

<button
  class={cn(
    "relative flex w-16 cursor-pointer flex-col items-center rounded-md py-1.5 transition-colors hover:bg-accent",
    active ? "text-primary" : "text-foreground/85 hover:text-primary",
    className,
  )}
  onclick={handleClick}
  disabled={!onclick}
  aria-label="Open configuration: {config.name}"
  title={config.name}
>
  {#if active}
    <!--
      The select indicator sits in the gutter left of the item box (the padding
      of the holder), so it never runs under the name: the name is centered on
      the whole box, an indicator inside the box would overlap the text, and
      the box is too narrow to inset the name away from it.
    -->
    <div class="absolute top-2 bottom-2 -left-1 w-1 rounded-full bg-primary"></div>
  {/if}
  <div class="relative">
    <ModIcon mod={config.mod} afspin={spin} />
    {#if startupUpdate}
      <!--
        The update of the process start: top right of the icon, the opposite
        corner of the state badge below, so both are readable at once. The halo
        keeps the arrow legible over the mod icon; the offset is one halo wider
        than the state badge's (-right-1.5 against -right-1), so the two icons
        line up on their right edge instead of the halo shifting this one in.
      -->
      <div data-slot="startup-update" class="absolute -top-1.5 -right-1.5 rounded-full bg-background p-0.5">
        <ConfigState workerState="updating" {active} />
      </div>
    {/if}
    <div data-slot="state-badge" class="absolute -right-1 bottom-0">
      <ConfigState workerState={displayState.value} {active} />
    </div>
  </div>
  <span class="line-clamp-2 text-center text-xs break-all" aria-hidden="true">
    {config.name}
  </span>
</button>
