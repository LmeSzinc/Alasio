<script lang="ts">
  import CircleDotDashed from "@lucide/svelte/icons/circle-dot-dashed";
  import Hourglass from "@lucide/svelte/icons/hourglass";
  import ConfigState from "$lib/components/aside/ConfigState.svelte";
  import { useWorkerState } from "$lib/components/aside/state.svelte";
  import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import ActionCancelResume from "./ActionCancelResume.svelte";
  import ActionKill from "./ActionKill.svelte";
  import ActionSchedulerContinue from "./ActionSchedulerContinue.svelte";
  import ActionSchedulerStop from "./ActionSchedulerStop.svelte";
  import ActionStart from "./ActionStart.svelte";
  import ConfigName from "./ConfigName.svelte";
  import NextRun from "./NextRun.svelte";
  import type { RestartPhase, TaskItem } from "./types";

  type $props = {
    config_name: string;
    workerState?: WORKER_STATE;
    taskRunning?: string;
    /** Due tasks of the queue (NextRun reached), the running task included */
    taskPending?: TaskItem[];
    /** Tasks scheduled later (NextRun in the future) */
    taskWaiting?: TaskItem[];
    /**
     * Restart phase override, e.g. for the dev page. Undefined reads the
     * Restart topic (`null` = no restart in progress).
     */
    restartPhase?: RestartPhase | null;
    onOverviewClick?: () => void;
    class?: string;
  };
  let {
    config_name,
    workerState = "idle",
    taskRunning,
    taskPending = [],
    taskWaiting = [],
    restartPhase: restartPhaseOverride,
    onOverviewClick,
    class: className,
  }: $props = $props();

  const displayState = useWorkerState(() => workerState);
  const isRunning = $derived(taskRunning && displayState.value !== "idle");
  // The circle icon of the task rows spins while a task runs; the pending half
  // of the summary row follows the same rule (its waiting half never spins)
  const spinTaskIcon = $derived(isRunning && displayState.value !== "error");

  // Restart topic: a non-empty phase means a graceful backend restart is in
  // progress ('done' is pushed right before the topic is cleared)
  const restartClient = useTopic<RestartTopicLike>("Restart");
  const restartPhase = $derived(
    restartPhaseOverride === undefined ? (restartClient.data?.phase ?? null) : restartPhaseOverride,
  );
  const isBackendRestarting = $derived(restartPhase !== null && restartPhase !== "done");
  // 'shutting-down' = the resume list is frozen and the backend is about to
  // exit: a config recorded for the resume cannot cancel it any more (the
  // backend refuses, the config resumes after the restart)
  const isResumeFrozen = $derived(restartPhase === "shutting-down");

  // Show 3 tasks, or 2 if a task is running (the running task is filtered out
  // of the queue lists, it is never shown as a next task). Each row keeps the
  // list it came from: a due (pending) row spins its circle, a later (waiting)
  // row shows the static hourglass.
  let nextTasksToShow = $derived.by(() => {
    let rows = [
      ...taskPending.map((task) => ({ task, waiting: false })),
      ...taskWaiting.map((task) => ({ task, waiting: true })),
    ];
    if (isRunning) {
      rows = rows.filter((row) => row.task.TaskName !== taskRunning);
    }
    const limit = 3 - (isRunning ? 1 : 0);
    return rows.slice(0, limit);
  });

  // Summary of the whole task table: the task rows only show the head of the
  // queue, the summary carries the totals of both lists (a zero side is dropped)
  const hasTaskSummary = $derived(taskPending.length > 0 || taskWaiting.length > 0);

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
  const killKeepResumeRpc = workerClient.rpc();
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
    // during a graceful restart wait the default kill also cancels the resume
    killRpc.call("kill", { config: config_name });
  }
  function handleKillKeepResume(e: Event) {
    e.stopPropagation();
    // force stop now, the worker is still resumed after the backend restart
    killKeepResumeRpc.call("kill", { config: config_name, restart_resume: true });
  }
  function handleCancelResume(e: Event) {
    e.stopPropagation();
    // "restarting" / "resuming" entry: stop = cancel the auto-resume
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
      {:else if workerState === "restarting"}{t.Scheduler.Restarting()}
      {:else if workerState === "resuming"}{t.Scheduler.Resuming()}
      {:else}{workerState}{/if}
    </span>
  </div>

  <hr class="mb-1" />

  <!-- Task list: always 4 rows tall (up to 3 tasks + the summary row in the
       last one), so the card height never follows the queue content. A row is
       one text-xs line (1rem) and the empty rows of a short queue keep their
       track. No bottom padding: the last row ends the block. -->
  <div class="mb-1 grid grid-rows-[repeat(4,1rem)] gap-0.5 pt-0.5 text-sm">
    {#if taskRunning || nextTasksToShow.length > 0}
      <!-- Task running -->
      {#if isRunning}
        <div class="flex items-center gap-1">
          <ConfigState {workerState} displayIdle={true} iconClass="h-3 w-3" class="shrink-0" />
          <span class="flex-1 truncate text-xs">{taskRunning}</span>
          <span class="min-w-8 shrink-0 text-right text-xs">now</span>
        </div>
      {/if}
      <!-- Task next: the icon tells due (pending, circle) from later (waiting,
           hourglass); a due row spins while a task runs, the waiting rows and
           the hourglass never spin. The hourglass is a solid glyph (the circles
           are dashed): /70 compensates its heavier look -->
      {#each nextTasksToShow as row}
        <div class="text-muted-foreground flex items-center gap-1">
          {#if row.waiting}
            <Hourglass class="text-muted-foreground/70 h-3 w-3 shrink-0" strokeWidth="2" />
          {:else}
            <CircleDotDashed
              class={cn("text-muted-foreground h-3 w-3 shrink-0", spinTaskIcon ? "animate-spin" : "")}
              strokeWidth="2"
            />
          {/if}
          <span class="flex-1 truncate text-xs">{row.task.TaskName}</span>
          <!-- now, hh:mm, >24h -->
          <NextRun timestamp={row.task.NextRun} class="min-w-8 shrink-0 text-right text-xs" />
        </div>
      {/each}
      <!-- Summary row: pending (due) and waiting (scheduled) halves of the
           whole task table, laid out in one line with a gap, left aligned. A
           zero half is dropped and the other keeps its place; the pending
           icon spins with the task rows, the hourglass never does. -->
      {#if hasTaskSummary}
        <div class="text-muted-foreground row-start-4 flex items-center gap-2">
          {#if taskPending.length > 0}
            <div class="flex min-w-0 items-center gap-1">
              <CircleDotDashed class={cn("h-3 w-3 shrink-0", spinTaskIcon ? "animate-spin" : "")} strokeWidth="2" />
              <span class="min-w-0 truncate text-xs">{t.Scheduler.PendingCount({ count: taskPending.length })}</span>
            </div>
          {/if}
          {#if taskWaiting.length > 0}
            <div class="flex min-w-0 items-center gap-1">
              <Hourglass class="text-muted-foreground/70 h-3 w-3 shrink-0" strokeWidth="2" />
              <span class="min-w-0 truncate text-xs">{t.Scheduler.WaitingCount({ count: taskWaiting.length })}</span>
            </div>
          {/if}
        </div>
      {/if}
    {:else if showNoTask}
      <div class="text-muted-foreground flex items-center justify-center gap-1">
        <span class="shrink-0 text-xs">{t.Scheduler.NoTask()}</span>
      </div>
    {/if}
  </div>

  <!-- Buttons: fixed h-7 row so the card height does not follow the button
       sizes (the filled pill is h-6, the outlined pill and the icon buttons
       are h-7) -->
  <div class="flex h-7 items-center gap-1">
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
      {#if isBackendRestarting}
        <!-- backend graceful restart: the wide button force-stops and keeps the
             auto-resume, the round X stops without resuming (the default kill
             cancels the pending resume) -->
        <ActionKill onclick={handleKillKeepResume} title={t.Scheduler.KillKeepResume()} class="flex-1" />
        <ActionCancelResume onclick={handleKill} title={t.Scheduler.KillNoResume()} />
      {:else}
        <!-- scheduler-stopping: kill (flex-1) + scheduler continue (right) -->
        <ActionKill disabled={isStoppingDebouncing} onclick={handleKill} title={t.Scheduler.Kill()} class="flex-1" />
        <ActionSchedulerContinue
          disabled={isStoppingDebouncing}
          onclick={handleSchedulerContinue}
          title={t.Scheduler.SchedulerContinue()}
        />
      {/if}
    {:else if displayState.value === "restarting"}
      <!-- restarting: the backend will resume it, start is disabled; the round
           button cancels the auto-resume (disabled once the resume list is
           frozen: 'shutting-down' is beyond the point of no return) -->
      <ActionStart disabled title={t.Scheduler.Start()} class="flex-1" />
      <ActionCancelResume onclick={handleCancelResume} disabled={isResumeFrozen} title={t.Scheduler.CancelResume()} />
    {:else if displayState.value === "resuming"}
      <!-- queued for auto-resume: start is refused (it starts by itself soon),
           a stop cancels the resume -->
      <ActionStart disabled title={t.Scheduler.Start()} class="flex-1" />
      <ActionCancelResume onclick={handleCancelResume} title={t.Scheduler.CancelResume()} />
    {:else if displayState.value === "killing" || displayState.value === "force-killing" || displayState.value === "disconnected"}
      <!-- killing: kill (flex-1) + scheduler stop (right) -->
      <ActionKill disabled title={t.Scheduler.Kill()} class="flex-1" />
      <ActionSchedulerStop disabled title={t.Scheduler.SchedulerStop()} />
    {/if}
  </div>
</div>
