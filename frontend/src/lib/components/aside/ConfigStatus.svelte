<script lang="ts">
  import { cn } from "$lib/utils";
  import { Circle, CircleDotDashed, CirclePlay, Ghost, Pause } from "@lucide/svelte";
  import { onDestroy, onMount, untrack } from "svelte";
  import type { WORKER_STATUS } from "./types";

  // props
  type Props = {
    status: WORKER_STATUS;
    active?: boolean;
    class?: string;
  };
  let { status, active = false, class: className }: Props = $props();

  // Track the displayed status and delay for intermediate states
  let displayedStatus = $state<WORKER_STATUS>("idle");
  let statusTimer: number | undefined = $state(undefined);

  // Define stable states (that should be displayed immediately)
  const STABLE_STATES: WORKER_STATUS[] = ["idle", "running", "scheduler-waiting", "error", "scheduler-stopping"];
  const spin = $derived(
    displayedStatus === "running" || displayedStatus === "scheduler-waiting" ? "animate-spin" : "",
  );

  // Effect to handle status changes with delay logic
  $effect(() => {
    const newStatus = status;

    // Clear any pending timer
    untrack(() => {
      if (statusTimer !== undefined) {
        clearTimeout(statusTimer);
        statusTimer = undefined;
      }
    });

    // If the new status is stable, display it immediately
    if (STABLE_STATES.includes(newStatus)) {
      displayedStatus = newStatus;
    } else {
      // Intermediate state: wait 300ms before displaying
      statusTimer = window.setTimeout(() => {
        displayedStatus = newStatus;
      }, 300);
    }
  });
  onDestroy(() => {
    if (statusTimer !== undefined) {
      clearTimeout(statusTimer);
    }
  });

  // global animation offset
  let delay = $state<string>("0ms");
  onMount(() => {
    const currentTime = Number(document.timeline?.currentTime ?? performance.now());
    const offset = -(currentTime % 1000);
    delay = `${offset}ms`;
  });
</script>

<div class={cn(spin, className)} style:animation-delay={delay}>
  {#if displayedStatus === "running"}
    <!-- Running: solid circle with theme color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2.5" aria-label="Running" />
  {:else if displayedStatus === "scheduler-waiting"}
    <!-- Scheduler waiting: hollow circle with theme color -->
    <CircleDotDashed class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2" aria-label="Waiting" />
  {:else if displayedStatus === "error"}
    <!-- Error: red X -->
    <Ghost class={cn("text-destructive h-3 w-3")} strokeWidth="2.5" aria-label="Error" />
  {:else if displayedStatus === "scheduler-stopping"}
    <!-- Scheduler stopping: pause icon (two vertical lines) -->
    <Pause class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2" aria-label="Stopping" />
  {:else if displayedStatus === "starting"}
    <!-- Starting: hollow circle with muted color -->
    <CirclePlay class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2" aria-label="Starting" />
  {:else if displayedStatus === "killing" || displayedStatus === "force-killing"}
    <!-- Killing: X with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2.5" aria-label="Killing" />
  {:else if displayedStatus === "disconnected"}
    <!-- Disconnected: circle with muted color -->
    <Ghost class={cn("h-3 w-3", !active && "text-primary")} strokeWidth="2" aria-label="Disconnected" />
  {/if}
  <!-- idle: no display -->
</div>
