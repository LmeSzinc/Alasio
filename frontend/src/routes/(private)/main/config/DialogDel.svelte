<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { AlertTriangle } from "@lucide/svelte";
  import type { Rpc } from "$lib/ws/rpc.svelte";
  import type { Config } from "./ConfigItem.svelte";

  type Props = {
    rpc: Rpc;
    targetConfig: Config | null;
  };
  let { rpc, targetConfig }: Props = $props();

  function handleSubmit(event: Event) {
    event.preventDefault();
    if (!targetConfig) return;

    rpc.call("config_del", { name: targetConfig.name });
  }

  function resetForm() {
    rpc.reset();
  }

  // Reset form when dialog opens
  $effect(() => {
    if (rpc.isOpen) {
      resetForm();
    }
  });
</script>

<Dialog bind:open={rpc.isOpen}>
  <DialogContent class="sm:max-w-md">
    <DialogHeader>
      <DialogTitle class="flex items-center gap-2">
        <AlertTriangle class="text-destructive h-5 w-5" />
        Delete Configuration
      </DialogTitle>
    </DialogHeader>

    <div class="space-y-4">
      <div class="text-sm">
        <p class="mb-2">Are you sure you want to delete this configuration?</p>
        <div class="bg-card text-card-foreground flex h-12 items-center rounded-md border p-2 shadow-sm">
          <div class="ml-2 flex-grow font-mono text-sm">
            {targetConfig?.name || "Unknown"}
          </div>
          {#if targetConfig?.mod}
            <div class="bg-secondary text-secondary-foreground ml-4 rounded px-2 py-1 text-xs">
              {targetConfig.mod}
            </div>
          {/if}
        </div>
        <p class="text-destructive mt-2 text-xs">
          This action cannot be undone. The configuration file will be permanently deleted.
        </p>
      </div>

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </div>

    <DialogFooter>
      <Button variant="outline" onclick={() => (rpc.isOpen = false)} disabled={rpc.isPending}>Cancel</Button>
      <Button variant="destructive" onclick={handleSubmit} disabled={rpc.isPending || !targetConfig}>
        Delete Configuration
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
