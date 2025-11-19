<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import { t } from "$lib/i18n";
  import type { Rpc } from "$lib/ws";
  import { Copy } from "@lucide/svelte";
  import type { Config } from "./ConfigItem.svelte";

  type Props = {
    rpc: Rpc;
    sourceConfig: Config | null;
  };
  let { rpc, sourceConfig }: Props = $props();

  let newName = $state("");

  // Reactive validation
  const isFormValid = $derived(newName.trim().length > 0 && sourceConfig !== null);

  function handleSubmit(event: Event) {
    event.preventDefault();
    const trimmedName = newName.trim();
    if (!sourceConfig || !trimmedName) return;

    rpc.call("config_copy", {
      old_name: sourceConfig.name,
      new_name: trimmedName,
    });
  }

  function resetForm() {
    newName = "";
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
        <Copy class="h-4 w-4" />
        {t.ConfigScan.CopyConfig()}
      </DialogTitle>
    </DialogHeader>

    <form onsubmit={handleSubmit} class="space-y-4">
      <div class="space-y-2">
        <Label>{t.ConfigScan.CopyFrom()}</Label>
        <div class="bg-card text-card-foreground flex h-12 items-center rounded-md border p-2 shadow-sm">
          <div class="ml-2 flex-grow font-mono text-sm">
            {sourceConfig?.name || t.ConfigScan.Unknown()}
          </div>
          {#if sourceConfig?.mod}
            <div class="bg-secondary text-secondary-foreground ml-4 rounded px-2 py-1 text-xs">
              {sourceConfig.mod}
            </div>
          {/if}
        </div>
      </div>

      <div class="space-y-2">
        <Label for="new-config-name">{t.ConfigScan.CopyTo()}</Label>
        <Input
          id="new-config-name"
          class="bg-card"
          bind:value={newName}
          placeholder={t.ConfigScan.EnterNewConfigName()}
          disabled={rpc.isPending}
        />
      </div>

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </form>

    <DialogFooter>
      <Button variant="outline" onclick={() => (rpc.isOpen = false)} disabled={rpc.isPending}>
        {t.ConfigScan.Cancel()}
      </Button>
      <Button onclick={handleSubmit} disabled={rpc.isPending || !isFormValid}>
        {t.ConfigScan.CopyConfig()}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
