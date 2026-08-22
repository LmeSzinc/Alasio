<script lang="ts">
  import { onMount } from "svelte";
  import { t } from "$lib/i18n";

  interface Props {
    show: boolean;
  }

  let { show = $bindable() }: Props = $props();
  let isClosing = $state(false);
  let shutdownStage = $state<string>("");

  const stageMessages = $derived<Record<string, string>>({
    waiting: t.CloseDialog.WaitingBackend(),
    forcing: t.CloseDialog.ForcingBackend(),
    killing: t.CloseDialog.KillingBackend(),
  });

  onMount(() => {
    const unsubscribe = window.electronAPI.onShutdownStage((stage: string) => {
      shutdownStage = stage;
    });

    return unsubscribe;
  });

  function handleCancel() {
    if (!isClosing) {
      show = false;
    }
  }

  async function handleConfirm() {
    isClosing = true;
    shutdownStage = "waiting";
    await window.electronAPI.confirmClose();
  }
</script>

{#if show}
  <div class="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm" onclick={handleCancel}>
    <div
      class="bg-card text-card-foreground border-border w-96 rounded-lg border p-6 shadow-lg"
      onclick={(e) => e.stopPropagation()}
    >
      <h2 class="mb-4 text-2xl font-bold">{t.CloseDialog.Title()}</h2>
      <p class="text-muted-foreground mb-6">{t.CloseDialog.Message()}</p>

      {#if isClosing}
        <div class="flex flex-col items-center gap-4 py-4">
          <div class="border-border border-t-muted-foreground h-8 w-8 animate-spin rounded-full border-4"></div>
          <p class="text-muted-foreground text-sm">
            {stageMessages[shutdownStage] || t.CloseDialog.Closing()}
          </p>
        </div>
      {:else}
        <div class="flex justify-end gap-3">
          <button
            onclick={handleCancel}
            class="border-border hover:bg-accent hover:text-accent-foreground rounded border px-4 py-2 transition-colors"
          >
            {t.CloseDialog.Cancel()}
          </button>
          <button
            onclick={handleConfirm}
            class="bg-destructive text-destructive-foreground hover:bg-destructive/90 rounded px-4 py-2 transition-colors"
          >
            {t.CloseDialog.Confirm()}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}
