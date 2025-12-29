<script lang="ts">
  import { cn } from "$lib/utils";
  import { CircleDotDashed, CirclePlay, Ghost, Pause } from "@lucide/svelte";
  import { mode } from "mode-watcher";
  import { onMount } from "svelte";
  import { useWorkerStatus } from "./status.svelte";
  import type { WORKER_STATUS } from "./types";

  // props
  type Props = {
    status: WORKER_STATUS;
    active?: boolean;
    class?: string;
    iconClass?: string;
    displayIdle?: boolean;
  };
  let { status, active = false, class: className, iconClass, displayIdle = false }: Props = $props();

  const displayStatus = useWorkerStatus(() => status);
  const strokeWidth = $derived(mode.current === "dark" ? "3" : "2");
  const spin = $derived(
    displayStatus.value === "running" || displayStatus.value === "scheduler-waiting" ? "animate-spin" : "",
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
  {#if displayStatus.value === "running"}
    <!-- Running: solid circle with theme color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Running" />
  {:else if displayStatus.value === "scheduler-waiting"}
    <!-- Scheduler waiting: hollow circle with theme color -->
    <CircleDotDashed class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Waiting" />
  {:else if displayStatus.value === "error"}
    <!-- Error: red X -->
    <Ghost class={cn("text-destructive h-3 w-3", iconClass)} {strokeWidth} aria-label="Error" />
  {:else if displayStatus.value === "scheduler-stopping"}
    <!-- Scheduler stopping: pause icon (two vertical lines) -->
    <Pause class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Stopping" />
  {:else if displayStatus.value === "starting"}
    <!-- Starting: hollow circle with muted color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Starting" />
  {:else if displayStatus.value === "killing" || displayStatus.value === "force-killing"}
    <!-- Killing: X with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Killing" />
  {:else if displayStatus.value === "disconnected"}
    <!-- Disconnected: circle with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Disconnected" />
  {:else if displayIdle && displayStatus.value === "idle"}
    <!-- idle: no display -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary", iconClass)} {strokeWidth} aria-label="Idle" />
  {/if}
</div>
