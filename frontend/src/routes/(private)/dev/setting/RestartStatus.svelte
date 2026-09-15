<script lang="ts">
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import Loader from "@lucide/svelte/icons/loader";
  import ConfigState from "$lib/components/aside/ConfigState.svelte";
  import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  /**
   * Status of a graceful backend restart, shown below the restart tool.
   *
   * The phase comes from the Restart topic and the configs still running (and
   * the ones queued for the auto-resume) come from the Worker topic, which is
   * the single source of truth for every worker state: the restart does not
   * keep a second copy of it. Without a restart in progress nothing is
   * rendered at all.
   */

  /** Phase of a graceful backend restart, `null` = no restart in progress */
  type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

  type Props = {
    /**
     * Phase override, e.g. for the dev page. Undefined reads the Restart
     * topic (`null` = no restart in progress).
     */
    phase?: RestartPhase | null;
    /**
     * Worker states override, e.g. for the dev page. Undefined reads the
     * Worker topic.
     */
    workers?: Record<string, WORKER_STATE>;
    class?: string;
  };
  let { phase: phaseOverride, workers: workersOverride, class: className }: Props = $props();

  // Restart topic: only carries the phase of the restart in progress
  const restartClient = useTopic<RestartTopicLike>("Restart");
  // Worker topic: per-worker progress of the restart ('restarting' = stopped
  // and waiting for the new backend, 'resuming' = queued for the auto-resume)
  const workerClient = useTopic<Record<string, WORKER_STATE>>("Worker");

  // Values come either from the props (dev page) or from the topics
  const phase = $derived(phaseOverride === undefined ? (restartClient.data?.phase ?? null) : phaseOverride);
  const workers = $derived(workersOverride === undefined ? (workerClient.data ?? {}) : workersOverride);

  // Configs the graceful stop is still waiting for: a worker that finished its
  // current task and stopped is marked "restarting" by the backend, and one
  // waiting in the auto-resume queue is "resuming" (no process either), so
  // every other alive state means "still running"
  const stoppingWorkers = $derived.by(() =>
    Object.entries(workers)
      .filter(([, state]) => state !== "restarting" && state !== "resuming" && state !== "idle" && state !== "error")
      .map(([name, state]) => ({ name, state })),
  );
  // Auto-resume queue of the new backend: waiting for its turn or starting
  const resumingWorkers = $derived.by(() =>
    Object.entries(workers)
      .filter(([, state]) => state === "resuming" || state === "starting")
      .map(([name, state]) => ({ name, state })),
  );
  // Already stopped, waiting for the new backend to start: "restarting" for
  // this restart and "resuming" for the queue it took over (a leftover of the
  // previous restart's auto-resume) -- neither has a process, both count as
  // stopped, and neither is a config the graceful stop still waits for
  const stoppedCount = $derived(
    Object.values(workers).filter((state) => state === "restarting" || state === "resuming").length,
  );

  type View = {
    phase: RestartPhase;
    workers: { name: string; state: WORKER_STATE }[];
  };

  // Hidden without a restart in progress. A frontend that reconnects in the
  // middle of a transaction can however miss the phase push: the worker states
  // then tell which stage the restart is in.
  const view = $derived.by<View | null>(() => {
    if (phase === null) {
      if (resumingWorkers.length) return { phase: "resuming", workers: resumingWorkers };
      if (stoppedCount) return { phase: "shutting-down", workers: [] };
      return null;
    }
    if (phase === "stopping") return { phase, workers: stoppingWorkers };
    if (phase === "resuming" || phase === "done") return { phase, workers: resumingWorkers };
    return { phase, workers: [] };
  });

  const phaseText = $derived(
    view?.phase === "stopping"
      ? t.DevTool.RestartPhaseStopping()
      : view?.phase === "shutting-down"
        ? t.DevTool.RestartPhaseShuttingDown()
        : view?.phase === "resuming"
          ? t.DevTool.RestartPhaseResuming()
          : view?.phase === "done"
            ? t.DevTool.RestartPhaseDone()
            : "",
  );

  // Graceful stop progress: stopped configs over the configs of this restart
  const progress = $derived({
    stopped: stoppedCount,
    total: stoppedCount + stoppingWorkers.length,
  });

  function stateLabel(state: WORKER_STATE) {
    if (state === "starting") return t.Scheduler.Starting();
    if (state === "running") return t.Scheduler.Running();
    if (state === "disconnected") return t.Scheduler.Disconnected();
    if (state === "scheduler-stopping") return t.Scheduler.SchedulerStopping();
    if (state === "scheduler-waiting") return t.Scheduler.SchedulerWaiting();
    if (state === "killing") return t.Scheduler.Killing();
    if (state === "force-killing") return t.Scheduler.ForceKilling();
    if (state === "restarting") return t.Scheduler.Restarting();
    if (state === "resuming") return t.Scheduler.Resuming();
    return t.Scheduler.Error();
  }
</script>

{#if view}
  <div class={cn("border-border bg-muted/40 flex flex-col gap-2 rounded-md border px-3 py-2.5", className)}>
    <!-- Phase -->
    <div class="flex items-center gap-x-2">
      {#if view.phase === "done"}
        <CircleCheck class="h-4 w-4 shrink-0" />
      {:else}
        <Loader class="text-primary h-4 w-4 shrink-0 animate-spin" />
      {/if}
      <span class="text-sm font-medium">{phaseText}</span>
      {#if view.phase === "stopping" && progress.total > 0}
        <span class="text-muted-foreground ml-auto shrink-0 text-xs">
          {t.DevTool.RestartProgress(progress)}
        </span>
      {/if}
    </div>

    <!-- Configs involved: still running, or queued for the auto-resume.
         Single column, one config per row: inside a row the name is left
         aligned and truncates, the state is right aligned (a long config name
         must not push the state away). The rule above the list goes with the
         list itself: without configs there is nothing to separate -->
    {#if view.workers.length > 0}
      <hr class="-my-1" />
      <div class="flex flex-col gap-y-1">
        {#each view.workers as worker (worker.name)}
          <div class="flex items-center gap-x-1.5 text-xs">
            <ConfigState workerState={worker.state} displayIdle class="shrink-0" iconClass="h-3 w-3" />
            <span class="min-w-0 flex-1 truncate font-medium" title={worker.name}>{worker.name}</span>
            <span class="text-muted-foreground shrink-0">{stateLabel(worker.state)}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}
