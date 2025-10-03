<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "$lib/components/ui/dialog";
  import { Help } from "$lib/components/ui/help";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import type { Rpc } from "$lib/ws/rpc.svelte";

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
      <DialogTitle>Add Configuration</DialogTitle>
    </DialogHeader>

    <form onsubmit={handleSubmit} class="space-y-4">
      <div class="space-y-2">
        <Label for="config-name">Configuration Name</Label>
        <Input id="config-name" bind:value={name} placeholder="Enter configuration name" disabled={rpc.isPending} />
      </div>

      <div class="space-y-2">
        <Label for="config-mod">Module</Label>
        <ModSelector bind:mod_name={mod} disabled={rpc.isPending} />
      </div>

      {#if rpc.errorMsg}
        <Help variant="error">{rpc.errorMsg}</Help>
      {/if}
    </form>

    <DialogFooter>
      <Button variant="outline" onclick={() => (rpc.isOpen = false)} disabled={rpc.isPending}>Cancel</Button>
      <Button onclick={handleSubmit} disabled={rpc.isPending || !isFormValid}>Add Configuration</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
