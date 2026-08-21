<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import type { Rpc } from "$lib/ws";

  type Props = {
    rpc: Rpc;
  };
  let { rpc }: Props = $props();

  function handleRestart(event: Event) {
    event.preventDefault();
    rpc.call("restart", {});
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

  $effect(() => {
    if (rpc.successMsg) {
      // RPC call succeeded, refresh the page
      // Force reload all
      window.location.reload();
    }
  });
</script>

<Dialog bind:open={rpc.isOpen}>
  <DialogContent class="sm:max-w-md">
    <DialogHeader>
      <DialogTitle>Are you sure you want to restart the backend?</DialogTitle>
    </DialogHeader>

    <div class="space-y-4">
      <p class="text-muted-foreground text-sm">
        All instances will be force-killed and restarted.
      </p>

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={handleCancel} disabled={rpc.isPending}>Cancel</Button>
      <Button variant="destructive" onclick={handleRestart} disabled={rpc.isPending}>Restart</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
