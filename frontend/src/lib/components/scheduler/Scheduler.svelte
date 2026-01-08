<script lang="ts">
  import ConfigStatus from "$lib/components/aside/ConfigStatus.svelte";
  import { useWorkerStatus } from "$lib/components/aside/status.svelte";
  import type { WORKER_STATUS } from "$lib/components/aside/types";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { CircleDotDashed, Pencil } from "@lucide/svelte";
  import ActionKill from "./ActionKill.svelte";
  import ActionStart from "./ActionStart.svelte";
  import ActionStop from "./ActionStop.svelte";
  import NextRun from "./NextRun.svelte";
  import type { TaskItem } from "./types";

  type $$props = {
    config_name: string;
    status?: WORKER_STATUS;
    deviceType?: string;
    deviceSerial?: string;
    taskRunning?: TaskItem;
    taskNext?: TaskItem[];
    onOverviewClick?: () => void;
    onDeviceClick?: () => void;
  };
  let {
    config_name,
    status = "idle",
    deviceType = "",
    deviceSerial = "",
    taskRunning,
    taskNext,
    onOverviewClick,
    onDeviceClick,
  }: $$props = $props();

  const displayStatus = useWorkerStatus(() => status);
  const isRunning = $derived(
    taskRunning && (displayStatus.value === "running" || displayStatus.value === "scheduler-waiting"),
  );
  const displaySerial = $derived(
    deviceSerial.startsWith("127.0.0.1:") ? deviceSerial.replace("127.0.0.1:", "") : deviceSerial,
  );

  // Show 3 tasks, or 2 if a task is running
  let nextTasksToShow = $derived.by(() => {
    const limit = 3 - (taskRunning ? 1 : 0);
    return taskNext?.slice(0, limit) || [];
  });

  function handleDeviceEdit(e: Event) {
    e.stopPropagation();
    onDeviceClick?.();
  }

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
  class="border-muted-foreground/35 relative flex max-w-60 flex-col gap-1.5 p-3"
  onclick={onOverviewClick}
  onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onOverviewClick?.()}
  role="button"
  tabindex="0"
>
  <div>
    <!-- Title -->
    <div class="flex items-center gap-2">
      <!-- Config Name -->
      <span class="flex-1 truncate text-lg font-semibold">{config_name}</span>
      <!-- Worker Status -->
      <span class="text-primary ml-auto pl-2 text-sm font-semibold">
        {#if status === "idle"}{t.Scheduler.Idle()}
        {:else if status === "starting"}{t.Scheduler.Starting()}
        {:else if status === "running"}{t.Scheduler.Running()}
        {:else if status === "disconnected"}{t.Scheduler.Disconnected()}
        {:else if status === "error"}{t.Scheduler.Error()}
        {:else if status === "scheduler-stopping"}{t.Scheduler.SchedulerStopping()}
        {:else if status === "scheduler-waiting"}{t.Scheduler.SchedulerWaiting()}
        {:else if status === "killing"}{t.Scheduler.Killing()}
        {:else if status === "force-killing"}{t.Scheduler.ForceKilling()}
        {:else}{status}{/if}
      </span>
    </div>

    <!-- Device -->
    <div class="text-muted-foreground flex w-full gap-0.5 text-xs">
      {#if deviceType && displaySerial}
        <span>{deviceType}</span>
        <span>-</span>
        <span class="truncate">{displaySerial}</span>
      {:else if displaySerial}
        <span class="truncate">{displaySerial}</span>
      {:else if deviceType}
        <span class="truncate">{deviceType}</span>
      {:else}
        <span class="truncate">{t.Scheduler.NoDevice()}</span>
      {/if}
      <button class="ml-1 cursor-pointer" onclick={handleDeviceEdit} aria-label="Edit device">
        <Pencil
          class={cn(
            "h-2.5 w-2.5",
            "border-muted-foreground/50 hover:text-foreground hover:border-foreground border-b transition-colors",
          )}
        />
      </button>
    </div>
  </div>

  <hr class="" />

  <!-- Task list -->
  <div class="flex h-12 flex-col text-sm">
    {#if !taskRunning && nextTasksToShow.length === 0}
      <div class="text-muted-foreground flex items-center justify-center gap-1">
        <span class="shrink-0 text-xs">{t.Scheduler.NoTask()}</span>
      </div>
    {:else}
      <!-- Task running -->
      {#if taskRunning}
        <div class="flex items-center gap-1">
          <ConfigStatus {status} displayIdle={true} iconClass="h-3 w-3" class="shrink-0" />
          <span class="flex-1 truncate text-xs">{taskRunning.TaskName}</span>
          <span class="min-w-8 shrink-0 text-right text-xs">now</span>
        </div>
      {/if}
      <!-- Task next -->
      {#each nextTasksToShow as task}
        <div class="text-muted-foreground flex items-center gap-1">
          <CircleDotDashed
            class={cn("text-muted-foreground h-3 w-3 shrink-0", isRunning ? "animate-spin" : "")}
            strokeWidth="2"
          />
          <span class="flex-1 truncate text-xs">{task.TaskName}</span>
          <!-- now, hh:mm, >24h -->
          <NextRun timestamp={task.NextRun} class="min-w-8 shrink-0 text-right text-xs" />
        </div>
      {/each}
    {/if}
  </div>

  <!-- Buttons -->
  <div class="mt-0.5 flex gap-1">
    {#if displayStatus.value === "idle" || displayStatus.value === "error"}
      <!-- idle, show one start button-->
      <ActionStart onclick={handleStart} title={t.Scheduler.Start()} />
    {:else if displayStatus.value === "starting"}
      <!-- starting, show one start button-->
      <ActionStart disabled title={t.Scheduler.Start()} />
    {:else if displayStatus.value === "running" || displayStatus.value === "scheduler-waiting"}
      <!-- running, show stop and kill button-->
      <ActionStop onclick={handleSchedulerStop} title={t.Scheduler.SchedulerStop()} />
      <ActionKill onclick={handleKill} />
    {:else if displayStatus.value === "scheduler-stopping"}
      <!-- scheduler-stopping, show continue and kill button-->
      <ActionStop onclick={handleSchedulerContinue} title={t.Scheduler.SchedulerContinue()} />
      <ActionKill onclick={handleKill} />
    {:else if displayStatus.value === "killing" || displayStatus.value === "force-killing" || displayStatus.value === "disconnected"}
      <!-- killing, show kill button-->
      <ActionStop disabled title={t.Scheduler.SchedulerStop()} />
      <ActionKill disabled title={t.Scheduler.Kill()} />
    {/if}
  </div>
</div>
