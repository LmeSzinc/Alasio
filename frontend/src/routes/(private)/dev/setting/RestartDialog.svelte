<script lang="ts">
  import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { t } from "$lib/i18n";
  import { type Rpc, useTopic } from "$lib/ws";

  type Props = {
    rpc: Rpc;
    /** Which restart the dialog confirms */
    kind: "graceful" | "force";
  };
  let { rpc, kind }: Props = $props();

  const isGraceful = $derived(kind === "graceful");

  // Restart topic: a non-empty phase means a graceful restart is running
  // ('done' is pushed right before the topic is cleared)
  const restartClient = useTopic<RestartTopicLike>("Restart");
  const phase = $derived(restartClient.data?.phase);
  const restartActive = $derived(!!phase && phase !== "done");

  // Progress counts from the Worker topic (single source of truth): the whole
  // state is maintained by the backend, restart does not keep a second copy
  const workerClient = useTopic<Record<string, WORKER_STATE>>("Worker");
  const progress = $derived.by(() => {
    const states = Object.values(workerClient.data ?? {});
    return {
      stopped: states.filter((state) => state === "restarting").length,
      total: states.length,
    };
  });

  const phaseText = $derived(
    phase === "stopping"
      ? t.DevTool.RestartPhaseStopping()
      : phase === "shutting-down"
        ? t.DevTool.RestartPhaseShuttingDown()
        : phase === "resuming"
          ? t.DevTool.RestartPhaseResuming()
          : "",
  );

  // The progress view replaces the confirmation while a graceful restart runs
  const showProgress = $derived(isGraceful && restartActive);

  // A graceful restart is accepted immediately and the shared rpc helper
  // closes the dialog on success; keep the dialog open as the progress view
  // until the restart finished (phase gone), then leave the view
  let submitted = $state(false);
  let phaseSeen = $state(false);
  $effect(() => {
    if (!isGraceful || !submitted) return;
    if (restartActive) {
      phaseSeen = true;
      rpc.isOpen = true;
    } else if (phaseSeen) {
      phaseSeen = false;
      submitted = false;
      rpc.isOpen = false;
      rpc.reset();
    }
  });

  function handleRestart(event: Event) {
    event.preventDefault();
    if (isGraceful) {
      submitted = true;
      phaseSeen = false;
      rpc.call("restart", {});
    } else {
      rpc.call("force_restart", {});
    }
  }

  function handleCancel() {
    rpc.reset();
    rpc.isOpen = false;
    submitted = false;
    phaseSeen = false;
  }

  // Reset error when dialog opens
  $effect(() => {
    if (rpc.isOpen) {
      rpc.reset();
    }
  });
</script>

<Dialog bind:open={rpc.isOpen}>
  <DialogContent class="sm:max-w-md">
    <DialogHeader>
      <DialogTitle>
        {isGraceful ? t.DevTool.RestartBackendConfirmTitle() : t.DevTool.ForceRestartBackendConfirmTitle()}
      </DialogTitle>
    </DialogHeader>

    <div class="space-y-4">
      {#if showProgress}
        <p class="text-muted-foreground text-sm">{phaseText}</p>
        {#if progress.total > 0}
          <p class="text-muted-foreground text-sm">
            {t.DevTool.RestartProgress(progress)}
          </p>
        {/if}
      {:else}
        <p class="text-muted-foreground text-sm">
          {isGraceful ? t.DevTool.RestartBackendConfirmHelp() : t.DevTool.ForceRestartBackendConfirmHelp()}
        </p>
      {/if}

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={handleCancel} disabled={rpc.isPending || showProgress}>
        {t.DevTool.Cancel()}
      </Button>
      <Button variant="destructive" onclick={handleRestart} disabled={rpc.isPending || showProgress}>
        {isGraceful ? t.DevTool.RestartBackend() : t.DevTool.ForceRestartBackend()}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
