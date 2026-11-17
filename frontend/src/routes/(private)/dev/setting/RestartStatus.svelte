<script lang="ts">
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import Loader from "@lucide/svelte/icons/loader";
  import ConfigState from "$lib/components/aside/ConfigState.svelte";
  import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  /**
   * Status of a backend maintenance flow (restart / update), shown below the
   * restart tool.
   *
   * The phase comes from the Restart topic and the configs still running (and
   * the ones queued for the auto-resume) come from the Worker topic, which is
   * the single source of truth for every worker state: the flow does not keep
   * a second copy of it. The update flow pushes the same 'stopping' phase and
   * parks its workers in "updating" instead of "restarting", everything else
   * is identical. Without a flow in progress nothing is rendered at all.
   */

  /** Phase of a graceful backend restart, `null` = no restart in progress */
  type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

  /**
   * Worker states of a maintenance flow in progress: no process, waiting for
   * the new backend. The backend parks the workers in the state of the flow
   * that stopped them ("restarting" for a restart, "updating" for an update /
   * daily / startup flow), see WORKER_PARKED_STATE in the worker manager.
   */
  const PARKED_STATES: WORKER_STATE[] = ["restarting", "updating"];

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

  // Restart topic: only carries the phase of the maintenance flow in progress
  const restartClient = useTopic<RestartTopicLike>("Restart");
  // Worker topic: per-worker progress of the flow ('restarting' / 'updating' =
  // stopped and waiting for the new backend, 'resuming' = queued for the
  // auto-resume)
  const workerClient = useTopic<Record<string, WORKER_STATE>>("Worker");

  // Values come either from the props (dev page) or from the topics
  const phase = $derived(phaseOverride === undefined ? (restartClient.data?.phase ?? null) : phaseOverride);
  const workers = $derived(workersOverride === undefined ? (workerClient.data ?? {}) : workersOverride);

  // Configs the graceful stop is still waiting for: a worker that finished its
  // current task and stopped is parked by the backend ("restarting" for a
  // restart flow, "updating" for an update one), and one waiting in the
  // auto-resume queue is "resuming" (no process either), so every other alive
  // state means "still running"
  const stoppingWorkers = $derived.by(() =>
    Object.entries(workers)
      .filter(
        ([, state]) => !PARKED_STATES.includes(state) && state !== "resuming" && state !== "idle" && state !== "error",
      )
      .map(([name, state]) => ({ name, state })),
  );
  // Auto-resume queue of the new backend: waiting for its turn or starting
  const resumingWorkers = $derived.by(() =>
    Object.entries(workers)
      .filter(([, state]) => state === "resuming" || state === "starting")
      .map(([name, state]) => ({ name, state })),
  );
  // Already stopped, waiting for the new backend to start: the park states for
  // this flow and "resuming" for the queue it took over (a leftover of the
  // previous restart's auto-resume) -- none of them has a process, all count as
  // stopped, and none is a config the graceful stop still waits for
  const stoppedCount = $derived(
    Object.values(workers).filter((state) => PARKED_STATES.includes(state) || state === "resuming").length,
  );
  // Configs parked by a restart only (no update flow): the missing-phase
  // fallback below reads them. A config parked in "updating" is left out: its
  // flow keeps the backend running while it applies the files (the backend is
  // not shutting down yet), and the phase is what tells that stage
  const restartParkedCount = $derived(Object.values(workers).filter((state) => state === "restarting").length);

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
      if (restartParkedCount) return { phase: "shutting-down", workers: [] };
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
    if (state === "updating") return t.Scheduler.Updating();
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
