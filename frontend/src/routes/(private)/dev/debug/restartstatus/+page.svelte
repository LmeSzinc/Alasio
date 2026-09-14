<script lang="ts">
  // !!![svelte-drop-dev-page]!!!
  import ArgGroupCard from "$lib/components/arg/ArgGroupCard.svelte";
  import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import RestartBackendTool from "$private/dev/setting/RestartBackendTool.svelte";
  import RestartStatus from "$private/dev/setting/RestartStatus.svelte";

  // A restart is fast and transient, so every state of RestartStatus is
  // rendered here with explicit phase / worker states: the props override the
  // Restart and Worker topics the component subscribes in production.
  //
  // Every preview is the settings page composition and not the banner alone:
  // the System Tool card holding the restart tool row with the status banner
  // right below it, so the combination of the two components is what gets
  // checked here.

  type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

  type TestCase = {
    label: string;
    note: string;
    /** null = no restart in progress, the component renders nothing */
    phase: RestartPhase | null;
    workers: Record<string, WORKER_STATE>;
  };

  const cases: TestCase[] = [
    {
      label: "Idle",
      note: "No restart, no restarting worker: the banner renders nothing, only the tool row is left.",
      phase: null,
      workers: {},
    },
    {
      label: "Stopping",
      note: "cfg_a already stopped (restarting), cfg_b is running, cfg_c is finishing its task.",
      phase: "stopping",
      workers: { cfg_a: "restarting", cfg_b: "running", cfg_c: "scheduler-stopping" },
    },
    {
      label: "Stopping · one stopped",
      note: "One config of two stopped: the counter shows 1/2.",
      phase: "stopping",
      workers: { cfg_a: "restarting", cfg_b: "scheduler-waiting" },
    },
    {
      label: "Stopping · all stopped",
      note: "Every config stopped, the backend is about to write the resume file.",
      phase: "stopping",
      workers: { cfg_a: "restarting", cfg_b: "restarting" },
    },
    {
      label: "Stopping · no running config",
      note: "Restart with nothing running: the phase is shown without any config.",
      phase: "stopping",
      workers: {},
    },
    {
      label: "Stopping · long config name",
      note: "The name truncates, the state stays at the right edge of the row.",
      phase: "stopping",
      workers: {
        cfg_with_a_very_long_name_that_should_not_push_the_state_away: "running",
        cfg_b: "scheduler-stopping",
      },
    },
    {
      label: "Shutting down",
      note: "Every config stopped, the backend process is restarting.",
      phase: "shutting-down",
      workers: { cfg_a: "restarting", cfg_b: "restarting" },
    },
    {
      label: "Resuming",
      note: "New backend: cfg_a waits for its turn, cfg_b is starting, cfg_c already runs.",
      phase: "resuming",
      workers: { cfg_a: "resuming", cfg_b: "starting", cfg_c: "running" },
    },
    {
      label: "Resuming · queue done",
      note: "'done' is pushed right before the topic is cleared (transient).",
      phase: "done",
      workers: { cfg_a: "starting", cfg_b: "running" },
    },
    {
      label: "Reconnect · stopping state missed",
      note: "Phase gone but a config is still restarting, e.g. the frontend reconnected late.",
      phase: null,
      workers: { cfg_a: "restarting", cfg_b: "restarting" },
    },
    {
      label: "Reconnect · resuming state missed",
      note: "Phase gone but a config is queued for the auto-resume.",
      phase: null,
      workers: { cfg_a: "resuming" },
    },
    {
      label: "Many configs",
      note: "A long waiting list renders one config per row.",
      phase: "stopping",
      workers: {
        cfg_a: "restarting",
        cfg_b: "running",
        cfg_c: "scheduler-stopping",
        cfg_d: "scheduler-waiting",
        cfg_e: "restarting",
        cfg_f: "killing",
        cfg_g: "force-killing",
        cfg_h: "disconnected",
      },
    },
  ];
</script>

<div class="container mx-auto flex h-full w-full flex-col gap-6 overflow-auto p-4">
  <h1 class="text-3xl font-bold">RestartStatus Debug Page</h1>

  <section class="space-y-2">
    <div>
      <h2 class="text-lg font-semibold">Live</h2>
      <p class="text-muted-foreground text-xs">
        Subscribes the real Restart and Worker topics: the banner stays hidden unless a graceful restart is in progress.
        The tool buttons call the real rpc.
      </p>
    </div>
    <ArgGroupCard title="System Tool" class="max-w-180">
      <RestartBackendTool />
      <RestartStatus />
    </ArgGroupCard>
  </section>

  <div class="flex flex-col gap-6">
    {#each cases as testCase (testCase.label)}
      <section class="space-y-2">
        <div>
          <h2 class="text-lg font-semibold">{testCase.label}</h2>
          <p class="text-muted-foreground text-xs">{testCase.note}</p>
        </div>
        <ArgGroupCard title="System Tool" class="max-w-180">
          <RestartBackendTool />
          <RestartStatus phase={testCase.phase} workers={testCase.workers} />
        </ArgGroupCard>
        <pre
          class="text-muted-foreground text-xs break-all whitespace-pre-wrap">phase={testCase.phase} workers={JSON.stringify(
            testCase.workers,
          )}</pre>
      </section>
    {/each}
  </div>
</div>
