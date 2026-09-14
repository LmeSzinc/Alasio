<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { t } from "$lib/i18n";
  import type { Rpc } from "$lib/ws";

  type Props = {
    rpc: Rpc;
    /** Which restart the dialog confirms */
    kind: "graceful" | "force";
  };
  let { rpc, kind }: Props = $props();

  const isGraceful = $derived(kind === "graceful");

  function handleRestart(event: Event) {
    event.preventDefault();
    // Both rpcs return immediately (the graceful one only marks the workers and
    // waits inside the backend): the rpc helper closes the dialog on success
    // and the progress is shown by RestartStatus below the restart tool.
    rpc.call(isGraceful ? "restart" : "force_restart", {});
  }

  function handleCancel() {
    rpc.reset();
    rpc.isOpen = false;
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
      <p class="text-muted-foreground text-sm">
        {isGraceful ? t.DevTool.RestartBackendConfirmHelp() : t.DevTool.ForceRestartBackendConfirmHelp()}
      </p>

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={handleCancel} disabled={rpc.isPending}>
        {t.DevTool.Cancel()}
      </Button>
      <Button variant="destructive" onclick={handleRestart} disabled={rpc.isPending}>
        {isGraceful ? t.DevTool.RestartBackend() : t.DevTool.ForceRestartBackend()}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
