<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import XIcon from "@lucide/svelte/icons/x";
  import Arg from "$lib/components/arg/Arg.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import ConfigItem from "$lib/components/aside/ConfigItem.svelte";
  import type { WORKER_STATE } from "$lib/components/aside/types";
  // Every worker state, in display order (the array the WORKER_STATE type is
  // derived from): this page can never miss a state
  import { WORKER_STATES } from "$lib/components/aside/types";
  import type { ConfigLike } from "$lib/components/aside/types";
  import Scheduler from "$lib/components/scheduler/Scheduler.svelte";
  import type { RestartPhase, TaskItem } from "$lib/components/scheduler/types";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  // The config column of the sheet previews: fixed data so every preview shows
  // the same column (see MockConfigColumn)
  const MOCK_CONFIGS: ConfigLike[] = [
    { id: 1, name: "example_mod2", mod: "alas", gid: 0, iid: 1 },
    { id: 2, name: "alas", mod: "alas", gid: 0, iid: 2 },
    { id: 3, name: "main", mod: "alas", gid: 0, iid: 3 },
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

  // Names of the sheet previews: the drawer is narrower than the sidebar, so the
  // previews use a typical config and task name rather than the long-name cases
  // of the cards above
  const SHEET_CONFIG_NAME = "example_mod2";
  const SHEET_TASKS = {
    taskRunning: "重启游戏",
    taskPending: [
      { TaskName: "大世界每日", NextRun: 0 },
      { TaskName: "每日任务", NextRun: 0 },
    ],
    taskWaiting: [{ TaskName: "每日副本", NextRun: 3600 }],
  };

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
    updating:
      "Stopped for an update / daily / startup flow instead of a user restart, otherwise identical to 'restarting': the label reads updating, the auto-resume and the cancel behave the same.",
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
    option: [...WORKER_STATES],
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

{#snippet MockCloseButton()}
  <!-- The close button of the mobile nav sheet, rendered with the same component
       and the same classes the real one uses (Button variant=ghost size=icon-sm
       at top-2.5 right-2.5, moved by the sheet's closeClass), so the hover and
       focus states match the sheet instead of being a flat icon. Only the
       Sheet.Close wrapper is missing: it needs a Dialog context that a preview
       has no reason to set up. -->
  <Button
    variant="ghost"
    size="icon-sm"
    class="absolute top-2.5 right-2.5 z-10 @max-[360px]/sheet:right-1"
    onclick={() => {}}
  >
    <XIcon class="size-4" />
    <span class="sr-only">Close</span>
  </Button>
{/snippet}

<!-- The config column of the sheet, built from the real ConfigItem: the tiles,
     the mod icon with its fallback and the state badge are what the app draws.
     The data is fixed (three configs, all idle) instead of the ConfigAside that
     reads the ConfigScan topic: a preview following the live list would change
     under the reader and show a different column on every card. -->
{#snippet MockConfigColumn()}
  <div class="flex w-20 shrink-0 flex-col gap-1 border-r border-border p-1">
    {#each MOCK_CONFIGS as config (config.id)}
      <div class="px-1">
        <ConfigItem {config} state="idle" />
      </div>
    {/each}
  </div>
{/snippet}

<!-- One sheet preview: a box as wide as the sheet, with its config column and the
     nav column beside it, holding a scheduler in sheet mode. The box is the size
     container `sheet`, the same one the real sheet is, so the narrow breakpoint
     (a sheet under 360px moves the close button and the title row's reserve)
     shows up here exactly as it does in the app. -->
{#snippet sheetDrawer(sheetWidth: number, workerState: WORKER_STATE)}
  <div class="flex flex-col gap-1">
    <div class="font-mono text-xs text-muted-foreground">{workerState}</div>
    <div
      class="@container/sheet relative overflow-hidden rounded-md ring-1 ring-border"
      style={`width: ${sheetWidth}px`}
    >
      <div class="flex">
        {@render MockConfigColumn()}
        <div class="min-w-0 flex-1">
          <Scheduler mode="sheet" config_name={SHEET_CONFIG_NAME} {workerState} restartPhase={null} {...SHEET_TASKS} />
        </div>
      </div>
      {@render MockCloseButton()}
    </div>
  </div>
{/snippet}

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
  <div class="scheduler-cards flex flex-col gap-4">
    <!-- Normal task list -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Normal Task List (Running + Next)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="grid md:grid-cols-2 lg:grid-cols-3">
          {#each WORKER_STATES as workerState}
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
          <div class="text-xs text-muted-foreground">
            Queue: pending + waiting, each zero side (it drops out of the summary) and the empty queue
          </div>
          <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {#each QUEUE_CASES as counts (counts.label)}
              <div class="flex flex-col gap-1">
                <div class="font-mono text-xs text-muted-foreground">{counts.label}</div>
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
          {#each WORKER_STATES as workerState}
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
          {#each WORKER_STATES as workerState}
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
          {#each WORKER_STATES as workerState}
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
          <p class="text-xs text-muted-foreground">
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
                <p class="text-xs text-muted-foreground">{RESTART_STATE_NOTES[workerState]}</p>
              </div>
              <div class="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {#each RESTART_PHASES as phase (phase.label)}
                  <div class="flex flex-col gap-1">
                    <div class="font-mono text-xs text-muted-foreground">phase={phase.label}</div>
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

    <!-- Sheet mode: the drawer the mobile layout opens. Its nav column is as
         wide as the phone leaves it (the sheet is 3/4 of the viewport, capped at
         384px, minus the 80px config column), and the drawer's close button
         floats over the top right corner of the scheduler title row. One card
         per device size shows both ends of the range the sheet is used at: a
         360px phone leaves a sheet of 270px (190px column), a 500px phone one of
         375px (295px column). Below 360px the sheet switches to its narrow
         layout, so the first card previews that state. The ring draws the sheet
         edge without a border: a border would take 2px off the width being
         previewed. -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Sheet Mode — Narrow Sheet (360px phone: sheet 270px, column 190px)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="flex flex-wrap items-start gap-4">
          {#each WORKER_STATES as workerState (workerState)}
            {@render sheetDrawer(270, workerState)}
          {/each}
        </div>
      </Card.Content>
    </Card.Root>

    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Sheet Mode — Wide Sheet (500px phone: sheet 375px, column 295px)</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="flex flex-wrap items-start gap-4">
          {#each WORKER_STATES as workerState (workerState)}
            {@render sheetDrawer(375, workerState)}
          {/each}
        </div>
      </Card.Content>
    </Card.Root>
  </div>
</div>

<style>
  /* The page renders about a hundred schedulers, so their state icons animate
     for nearly 200 elements at once (a spinning arc on every running task row,
     the sway of a killing worker, the rising arrow of an updating one). While
     such an element is painted, every frame repaints it on the main thread,
     and a page that repaints hundreds of them per frame cannot also scroll.
     Measured here (1400x900, a 3000px scroll in 50 steps, one frame per step):
     2342ms with every scheduler painted against 834ms once the off-screen ones
     are skipped, i.e. 25 fps against 60 fps.

     content-visibility: auto takes a scheduler out of rendering while it is off
     screen, and an unrendered subtree does not animate either, so the cards in
     view keep their motion and the rest cost nothing. It is applied to the
     scheduler and not to the card that holds it: a scheduler has a fixed
     height, so the reserved space can be stated instead of guessed.

     The value is the height of the scheduler's content box, not of the element.
     `contain-intrinsic-size` replaces the content box, and the element's own
     padding is added on top: the scheduler measures 168.8px and carries pb-3
     (12px), so its content box is 156.8px and 157px is the exact estimate. 169px
     here would reserve 181px per card and shift the list by 330px on the first
     pass, and a value that is off by more moves it by thousands of pixels
     (measured: 60px grows the list by 2.5k px, 320px shrinks it by 4.2k px).
     The `auto` keyword remembers the measured height after the first render, so
     the estimate only covers the cards that have never been rendered. */
  .scheduler-cards :global([data-slot="scheduler"]) {
    content-visibility: auto;
    contain-intrinsic-size: auto 157px;
  }
</style>
