<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import { t } from "$lib/i18n";
  import type { Rpc } from "$lib/ws";
  import { Plus } from "@lucide/svelte";

  type Props = {
    rpc: Rpc;
  };
  let { rpc }: Props = $props();

  let name = $state("");
  let mod = $state("");

  // Reactive validation
  const isFormValid = $derived(name.trim().length > 0 && mod.length > 0);

  function handleSubmit(event: Event) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || !mod) return;

    rpc.call("config_add", { name: trimmedName, mod });
  }

  function resetForm() {
    name = "";
    mod = "";
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
        <Plus class="h-4 w-4" />
        {t.ConfigScan.AddConfig()}
      </DialogTitle>
    </DialogHeader>

    <form onsubmit={handleSubmit} class="space-y-4">
      <div class="space-y-2">
        <Label for="config-name">{t.ConfigScan.ConfigName()}</Label>
        <Input
          id="config-name"
          class="bg-card"
          bind:value={name}
          placeholder={t.ConfigScan.EnterConfigName()}
          disabled={rpc.isPending}
        />
      </div>

      <div class="space-y-2">
        <Label for="config-mod">{t.Mod.ModName()}</Label>
        <ModSelector bind:mod_name={mod} disabled={rpc.isPending} class="bg-card" />
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
        {t.ConfigScan.AddConfig()}
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
