<script lang="ts">
  import { mode } from "mode-watcher";
  import { onMount } from "svelte";
  import ArrowUp from "@lucide/svelte/icons/arrow-up";
  import CircleDotDashed from "@lucide/svelte/icons/circle-dot-dashed";
  import CirclePlay from "@lucide/svelte/icons/circle-play";
  import Ghost from "@lucide/svelte/icons/ghost";
  import Loader from "@lucide/svelte/icons/loader";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import SquarePause from "@lucide/svelte/icons/square-pause";
  import X from "@lucide/svelte/icons/x";
  import { cn } from "$lib/utils";
  import AnimationShake from "./AnimationShake.svelte";
  import AnimationUp from "./AnimationUp.svelte";
  import type { WORKER_STATE } from "./types";

  // props
  type Props = {
    workerState: WORKER_STATE;
    active?: boolean;
    class?: string;
    iconClass?: string;
    displayIdle?: boolean;
  };
  let { workerState, active = false, class: className, iconClass, displayIdle = false }: Props = $props();

  const strokeWidth = $derived(mode.current === "dark" ? "3" : "2");
  // Only the round / circular icons spin: the spin reads as the icon turning
  // on itself, and "updating" is a plain arrow whose direction carries the
  // meaning -- rotating it would point the arrow away from "up". The loader
  // of "resuming" is an open ring, which is what a spinner is made of.
  const spin = $derived(
    workerState === "running" ||
      workerState === "scheduler-waiting" ||
      workerState === "restarting" ||
      workerState === "resuming"
      ? "animate-spin"
      : "",
  );

  // global animation offset
  let delay = $state<string>("0ms");
  onMount(() => {
    const currentTime = Number(document.timeline?.currentTime ?? performance.now());
    const offset = -(currentTime % 1000);
    delay = `${offset}ms`;
  });
</script>

<div class={cn("pointer-events-none", spin, className)} style:animation-delay={delay}>
  {#if workerState === "running"}
    <!-- Running: solid circle with theme color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Running" />
  {:else if workerState === "scheduler-waiting"}
    <!-- Scheduler waiting: hollow circle with theme color -->
    <CircleDotDashed class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Waiting" />
  {:else if workerState === "error"}
    <!-- Error: red X -->
    <X class={cn("text-destructive h-3 w-3", iconClass)} {strokeWidth} aria-label="Error" />
  {:else if workerState === "scheduler-stopping"}
    <!-- Scheduler stopping: pause in a square. No sway: the worker simply
         finishes its task, which is the ordinary end of a run and not a fault,
         and the swing would not read at the 10px size of this icon -->
    <SquarePause class={cn("h-2.5 w-2.5", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Stopping" />
  {:else if workerState === "starting"}
    <!-- Starting: hollow circle with muted color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Starting" />
  {:else if workerState === "killing" || workerState === "force-killing"}
    <!-- Killing: the process is being torn down, a transient state that needs
         attention, so its ghost rings like a bell -->
    <AnimationShake>
      <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Killing" />
    </AnimationShake>
  {:else if workerState === "restarting"}
    <!-- Restarting: stopped for a graceful backend restart, auto-resume later -->
    <RefreshCw class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Restarting" />
  {:else if workerState === "updating"}
    <!-- Updating: stopped for an update transaction, auto-resume after it -->
    <AnimationUp class={cn("h-3 w-3", iconClass)}>
      <ArrowUp class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Updating" />
    </AnimationUp>
  {:else if workerState === "resuming"}
    <!-- Resuming: queued for auto-resume after the backend restart -->
    <Loader class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Resuming" />
  {:else if workerState === "disconnected"}
    <!-- Disconnected: the backend lost the worker, the same transient attention
         state as killing -->
    <AnimationShake>
      <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Disconnected" />
    </AnimationShake>
  {:else if displayIdle && workerState === "idle"}
    <!-- idle: no display -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Idle" />
  {/if}
</div>
