<script lang="ts">
  import { cn } from "$lib/utils";
  import { CircleDotDashed, CirclePlay, Ghost, Hourglass, X } from "@lucide/svelte";
  import { mode } from "mode-watcher";
  import { onMount } from "svelte";
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
  const spin = $derived(
    workerState === "running" || workerState === "scheduler-waiting" || workerState === "scheduler-stopping"
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
    <!-- Scheduler stopping: hourglass icon -->
    <Hourglass class={cn("h-2.5 w-2.5", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Stopping" />
  {:else if workerState === "starting"}
    <!-- Starting: hollow circle with muted color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Starting" />
  {:else if workerState === "killing" || workerState === "force-killing"}
    <!-- Killing: X with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Killing" />
  {:else if workerState === "disconnected"}
    <!-- Disconnected: circle with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Disconnected" />
  {:else if displayIdle && workerState === "idle"}
    <!-- idle: no display -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Idle" />
  {/if}
</div>
