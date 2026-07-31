<script lang="ts">
  import ConfigState from "$lib/components/aside/ConfigState.svelte";
  import { useWorkerState } from "$lib/components/aside/state.svelte";
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { CircleDotDashed } from "@lucide/svelte";
  import ActionKill from "./ActionKill.svelte";
  import ActionSchedulerContinue from "./ActionSchedulerContinue.svelte";
  import ActionSchedulerStop from "./ActionSchedulerStop.svelte";
  import ActionStart from "./ActionStart.svelte";
  import ConfigName from "./ConfigName.svelte";
  import NextRun from "./NextRun.svelte";
  import type { TaskItem } from "./types";

  type $$props = {
    config_name: string;
    workerState?: WORKER_STATE;
    taskRunning?: string;
    taskNext?: TaskItem[];
    onOverviewClick?: () => void;
    class?: string;
  };
  let {
    config_name,
    workerState = "idle",
    taskRunning,
    taskNext,
    onOverviewClick,
    class: className,
  }: $$props = $props();

  const displayState = useWorkerState(() => workerState);
  const isRunning = $derived(taskRunning && displayState.value !== "idle");

  // Show 3 tasks, or 2 if a task is running
  let nextTasksToShow = $derived.by(() => {
    let tasks = taskNext || [];
    if (isRunning) {
      tasks = tasks.filter((task) => task.TaskName !== taskRunning);
    }
    const limit = 3 - (isRunning ? 1 : 0);
    return tasks.slice(0, limit);
  });

  let showNoTask = $state(false);
  $effect(() => {
    if (taskRunning || nextTasksToShow.length > 0) {
      showNoTask = false;
    } else {
      const timer = setTimeout(() => {
        showNoTask = true;
      }, 500);
      return () => clearTimeout(timer);
    }
  });

  let isStoppingDebouncing = $state(false);
  $effect(() => {
    if (workerState === "scheduler-stopping") {
      isStoppingDebouncing = true;
      const timer = setTimeout(() => {
        isStoppingDebouncing = false;
      }, 1000);
      return () => {
        clearTimeout(timer);
        isStoppingDebouncing = false;
      };
    }
  });

  // RPCs
  const workerClient = useTopic("Worker");
  const startRpc = workerClient.rpc();
  const schedulerStopRpc = workerClient.rpc();
  const schedulerContinueRpc = workerClient.rpc();
  const killRpc = workerClient.rpc();
  function handleStart(e: Event) {
    e.stopPropagation();
    startRpc.call("start", { config: config_name });
  }
  function handleSchedulerStop(e: Event) {
    e.stopPropagation();
    schedulerStopRpc.call("scheduler_stop", { config: config_name });
  }
  function handleSchedulerContinue(e: Event) {
    e.stopPropagation();
    schedulerContinueRpc.call("scheduler_continue", { config: config_name });
  }
  function handleKill(e: Event) {
    e.stopPropagation();
    killRpc.call("kill", { config: config_name });
  }
</script>

<div
  class={cn("border-muted-foreground/35 relative flex max-w-60 flex-col px-3 pb-3", className)}
  onclick={onOverviewClick}
  onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onOverviewClick?.()}
  role="button"
  tabindex="0"
>
  <!-- Title -->
  <!-- Keep h-12 aligned with AppHeader bottom (h-12 = 48px) -->
  <!-- minor padding-left for visual compensation of title -->
  <div class="flex h-12 items-center gap-1 pl-0.25">
    <!-- Config Name -->
    <ConfigName text={config_name} class="w-30 shrink-0" />
    <!-- Worker Status -->
    <span
      class={cn(
        "ml-auto truncate text-right text-sm font-semibold",
        workerState === "error" ? "text-destructive" : "text-primary",
      )}
    >
      {#if workerState === "idle"}{t.Scheduler.Idle()}
      {:else if workerState === "starting"}{t.Scheduler.Starting()}
      {:else if workerState === "running"}{t.Scheduler.Running()}
      {:else if workerState === "disconnected"}{t.Scheduler.Disconnected()}
      {:else if workerState === "error"}{t.Scheduler.Error()}
      {:else if workerState === "scheduler-stopping"}{t.Scheduler.SchedulerStopping()}
      {:else if workerState === "scheduler-waiting"}{t.Scheduler.SchedulerWaiting()}
      {:else if workerState === "killing"}{t.Scheduler.Killing()}
      {:else if workerState === "force-killing"}{t.Scheduler.ForceKilling()}
      {:else}{workerState}{/if}
    </span>
  </div>

  <hr class="mb-1" />

  <!-- Task list -->
  <div class="mb-3 flex h-12 flex-col gap-0.5 py-0.5 text-sm">
    {#if taskRunning || nextTasksToShow.length > 0}
      <!-- Task running -->
      {#if isRunning}
        <div class="flex items-center gap-1">
          <ConfigState {workerState} displayIdle={true} iconClass="h-3 w-3" class="shrink-0" />
          <span class="flex-1 truncate text-xs">{taskRunning}</span>
          <span class="min-w-8 shrink-0 text-right text-xs">now</span>
        </div>
      {/if}
      <!-- Task next -->
      {#each nextTasksToShow as task}
        <div class="text-muted-foreground flex items-center gap-1">
          <CircleDotDashed
            class={cn(
              "text-muted-foreground h-3 w-3 shrink-0",
              isRunning && displayState.value !== "error" ? "animate-spin" : "",
            )}
            strokeWidth="2"
          />
          <span class="flex-1 truncate text-xs">{task.TaskName}</span>
          <!-- now, hh:mm, >24h -->
          <NextRun timestamp={task.NextRun} class="min-w-8 shrink-0 text-right text-xs" />
        </div>
      {/each}
    {:else if showNoTask}
      <div class="text-muted-foreground flex items-center justify-center gap-1">
        <span class="shrink-0 text-xs">{t.Scheduler.NoTask()}</span>
      </div>
    {/if}
  </div>

  <!-- Buttons -->
  <div class="flex gap-1">
    {#if displayState.value === "idle" || displayState.value === "error"}
      <!-- idle, show one start button-->
      <ActionStart onclick={handleStart} title={t.Scheduler.Start()} />
    {:else if displayState.value === "starting"}
      <ActionStart disabled title={t.Scheduler.Start()} />
    {:else if displayState.value === "running" || displayState.value === "scheduler-waiting"}
      <!-- running: kill (flex-1) + scheduler stop (right) -->
      <ActionKill onclick={handleKill} title={t.Scheduler.Kill()} class="flex-1" />
      <ActionSchedulerStop onclick={handleSchedulerStop} title={t.Scheduler.SchedulerStop()} />
    {:else if displayState.value === "scheduler-stopping"}
      <!-- scheduler-stopping: kill (flex-1) + scheduler continue (right) -->
      <ActionKill disabled={isStoppingDebouncing} onclick={handleKill} title={t.Scheduler.Kill()} class="flex-1" />
      <ActionSchedulerContinue
        disabled={isStoppingDebouncing}
        onclick={handleSchedulerContinue}
        title={t.Scheduler.SchedulerContinue()}
      />
    {:else if displayState.value === "killing" || displayState.value === "force-killing" || displayState.value === "disconnected"}
      <!-- killing: kill (flex-1) + scheduler stop (right) -->
      <ActionKill disabled title={t.Scheduler.Kill()} class="flex-1" />
      <ActionSchedulerStop disabled title={t.Scheduler.SchedulerStop()} />
    {/if}
  </div>
</div>
