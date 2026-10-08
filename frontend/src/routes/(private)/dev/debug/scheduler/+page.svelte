<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import Arg from "$lib/components/arg/Arg.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import Scheduler from "$lib/components/scheduler/Scheduler.svelte";
  import type { RestartPhase, TaskItem } from "$lib/components/scheduler/types";
  import * as Card from "$lib/components/ui/card";

  // All available state options
  const ALL_STATES: WORKER_STATE[] = [
    "idle",
    "starting",
    "running",
    "scheduler-stopping",
    "scheduler-waiting",
    "killing",
    "force-killing",
    "disconnected",
    "error",
    "restarting",
    "updating",
    "resuming",
  ];

  // Restart topic phases: the phase decides which buttons a stopping or
  // parked worker gets. "none" is the empty topic (no restart in progress),
  // which the component reads as `null`.
  const RESTART_PHASES: { label: string; phase: RestartPhase | null }[] = [
    { label: "none", phase: null },
    { label: "stopping", phase: "stopping" },
    { label: "shutting-down", phase: "shutting-down" },
    { label: "resuming", phase: "resuming" },
    { label: "done", phase: "done" },
  ];

  // Worker states whose buttons depend on the restart phase
  const RESTART_STATES: WORKER_STATE[] = ["scheduler-stopping", "restarting", "updating", "resuming"];

  /**
   * Preview queue of the scheduler: `pending` due tasks (the first one takes
   * the `running` name when given) and `waiting` tasks scheduled later. The
   * queue lists are the only source of the task rows and of the summary
   * counts, so a preview can never show counts that disagree with its rows.
   */
  function makeQueue(
    pending: number,
    waiting: number,
    running?: string,
  ): {
    taskPending: TaskItem[];
    taskWaiting: TaskItem[];
  } {
    const base = Math.floor(Date.now() / 1000);
    return {
      taskPending: Array.from({ length: pending }, (_, i) => ({
        TaskName: i === 0 && running ? running : `PendingTask${i + 1}`,
        NextRun: base,
      })),
      taskWaiting: Array.from({ length: waiting }, (_, i) => ({
        TaskName: `WaitingTask${i + 1}`,
        NextRun: base + 3600 * (i + 1),
      })),
    };
  }

  // Queue combinations of the summary row: both sides, each zero side (the
  // zero side is dropped there) and the empty queue
  const QUEUE_CASES: { label: string; pending: number; waiting: number; running: boolean }[] = [
    { label: "pending=24 waiting=3", pending: 24, waiting: 3, running: true },
    { label: "pending=0 waiting=3", pending: 0, waiting: 3, running: false },
    { label: "pending=24 waiting=0", pending: 24, waiting: 0, running: true },
    { label: "pending=0 waiting=0", pending: 0, waiting: 0, running: false },
  ];

  // What the phase changes for each of them
  const RESTART_STATE_NOTES: Record<string, string> = {
    "scheduler-stopping":
      "Finishing its current task. Without a restart: stop + continue. During a restart: stop now (resumes after the restart), or stop now without resuming (the round X).",
    restarting:
      "Stopped for the restart, no process, waiting for the new backend: start is disabled, the round button cancels the auto-resume. Disabled once the resume list is frozen ('shutting-down'): the backend refuses the cancel from there on.",
    resuming:
      "Queued for the auto-resume of the new backend, no process yet: start is disabled, the round button cancels the queue entry (never disabled, a stop here always cancels).",
  };

  // Input state for selected config
  let configNameInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "config_name",
    dt: "input",
    value: "MyConfig",
    name: "Config Name",
  });

  let stateInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "state",
    dt: "select",
    value: "idle",
    name: "State",
    option: ALL_STATES,
  });

  let queueInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "queue",
    dt: "select",
    value: "both",
    name: "Queue",
    option: ["both", "pending-only", "waiting-only", "empty", "running-only", "long-names"],
  });

  let restartPhaseInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "restart_phase",
    dt: "select",
    value: "none",
    name: "Restart Phase",
    option: RESTART_PHASES.map(({ label }) => label),
  });

  // Restart phase of the selected preview, "none" = no restart in progress
  const restartPhase = $derived(RESTART_PHASES.find(({ label }) => label === restartPhaseInput.value)?.phase ?? null);

  // Queue of the selected preview, driven by the Queue select
  const previewQueue = $derived.by(() => {
    const base = Math.floor(Date.now() / 1000);
    switch (queueInput.value) {
      case "pending-only":
        return { taskRunning: "CurrentTask", ...makeQueue(24, 0, "CurrentTask") };
      case "waiting-only":
        return { taskRunning: undefined, ...makeQueue(0, 3) };
      case "empty":
        return { taskRunning: undefined, taskPending: [], taskWaiting: [] };
      case "running-only":
        return { taskRunning: "CurrentTask", ...makeQueue(1, 0, "CurrentTask") };
      case "long-names":
        return {
          taskRunning: "VeryLongTaskNameThatShouldBeTruncatedInTheUI",
          taskPending: [{ TaskName: "VeryLongTaskNameThatShouldBeTruncatedInTheUI", NextRun: base }],
          taskWaiting: [
            { TaskName: "AnotherVeryLongTaskNameThatWillDefinitelyBeTruncated", NextRun: base + 1800 },
            { TaskName: "ShortTask", NextRun: base + 3600 },
          ],
        };
      default:
        return { taskRunning: "CurrentTask", ...makeQueue(24, 3, "CurrentTask") };
    }
  });
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <h1 class="text-3xl font-bold">Scheduler Component Debug Page</h1>

  <div class="grid gap-4 md:grid-cols-2">
    <!-- Selected Config Preview -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Selected Scheduler Preview</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="flex items-start gap-4">
          <div class="flex-1">
            <Scheduler
              config_name={configNameInput.value as string}
              workerState={stateInput.value as WORKER_STATE}
              {restartPhase}
              {...previewQueue}
              onOverviewClick={() => console.log("Overview clicked")}
            />
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Control Panel -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Control Panel</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="space-y-3">
          <Arg bind:data={configNameInput} />
          <Arg bind:data={stateInput} />
          <Arg bind:data={restartPhaseInput} />
          <Arg bind:data={queueInput} />
        </div>
      </Card.Content>
    </Card.Root>
  </div>

  <!-- All Status Combinations. The grids pass restartPhase={null} ("no
       restart in progress") to stay on a deterministic baseline: the restart
       dimension is previewed by the matrix at the end of the page -->
  <div class="flex flex-col gap-4">
    <!-- Normal task list -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Normal Task List (Running + Next)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATES as workerState}
            <Scheduler
              config_name="TestConfig"
              {workerState}
              restartPhase={null}
              taskRunning="CurrentTask"
              {...makeQueue(24, 3, "CurrentTask")}
            />
          {/each}
        </div>

        <!-- Summary row: both sides, each zero side and the empty queue -->
        <div class="mt-6 flex flex-col gap-2">
          <div class="text-muted-foreground text-xs">
            Queue: pending + waiting, each zero side (it drops out of the summary) and the empty queue
          </div>
          <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {#each QUEUE_CASES as counts (counts.label)}
              <div class="flex flex-col gap-1">
                <div class="text-muted-foreground font-mono text-xs">{counts.label}</div>
                <Scheduler
                  config_name="CountConfig"
                  workerState={counts.running ? "running" : "idle"}
                  restartPhase={null}
                  taskRunning={counts.running ? "CurrentTask" : undefined}
                  {...makeQueue(counts.pending, counts.waiting, counts.running ? "CurrentTask" : undefined)}
                />
              </div>
            {/each}
          </div>
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Empty task list -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Empty Task List</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATES as workerState}
            <Scheduler config_name="EmptyScheduler" {workerState} restartPhase={null} />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Long names -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Long Config and Task Names</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATES as workerState}
            <Scheduler
              config_name="VeryLongConfigurationNameThatShouldBeTruncatedInTheUserInterface"
              {workerState}
              restartPhase={null}
              taskRunning="VeryLongTaskNameThatWillBeTruncated"
              taskPending={[
                { TaskName: "VeryLongTaskNameThatWillBeTruncated", NextRun: Math.floor(Date.now() / 1000) },
              ]}
              taskWaiting={[
                {
                  TaskName: "AnotherVeryLongTaskNameForTesting",
                  NextRun: Math.floor(Date.now() / 1000) + 1800,
                },
              ]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Only next tasks -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Next Tasks Only (No Running Task)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each ALL_STATES as workerState}
            <Scheduler
              config_name="NextOnly"
              {workerState}
              restartPhase={null}
              taskWaiting={[
                { TaskName: "Future1", NextRun: Math.floor(Date.now() / 1000) + 600 },
                { TaskName: "Future2", NextRun: Math.floor(Date.now() / 1000) + 3600 },
                { TaskName: "Future3", NextRun: Math.floor(Date.now() / 1000) + 100000 },
              ]}
            />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <!-- Restart states: the Restart topic phase decides which buttons a
         stopping or parked worker gets -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Restart States (Worker × Restart Phase)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="flex flex-col gap-6">
          <p class="text-muted-foreground text-xs">
            Restart phase: <span class="font-mono">none</span> = no restart in progress,
            <span class="font-mono">stopping</span> = waiting for the workers to stop,
            <span class="font-mono">shutting-down</span> = all workers stopped, the backend is about to exit (the resume
            list is frozen), <span class="font-mono">resuming</span> = the new backend is starting the recorded workers,
            <span class="font-mono">done</span> = the resume queue was processed (transient).
          </p>
          {#each RESTART_STATES as workerState (workerState)}
            <div class="flex flex-col gap-2">
              <div>
                <h3 class="font-mono text-lg font-medium">{workerState}</h3>
                <p class="text-muted-foreground text-xs">{RESTART_STATE_NOTES[workerState]}</p>
              </div>
              <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {#each RESTART_PHASES as phase (phase.label)}
                  <div class="flex flex-col gap-1">
                    <div class="text-muted-foreground font-mono text-xs">phase={phase.label}</div>
                    <Scheduler
                      config_name="RestartConfig"
                      {workerState}
                      restartPhase={phase.phase}
                      taskRunning="CurrentTask"
                      {...makeQueue(24, 3, "CurrentTask")}
                    />
                  </div>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      </Card.Content>
    </Card.Root>
  </div>
</div>
