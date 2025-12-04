<script lang="ts">
  import { onMount } from "svelte";
  import * as t from "$lib/i18n/close-dialog";

  interface Props {
    show: boolean;
  }

  let { show = $bindable() }: Props = $props();
  let isClosing = $state(false);
  let shutdownStage = $state<string>("");

  const stageMessages = $derived({
    waiting: t.WaitingBackend(),
    forcing: t.ForcingBackend(),
    killing: t.KillingBackend(),
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
  <div class="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50" onclick={handleCancel}>
    <div
      class="bg-card text-card-foreground rounded-lg p-6 w-96 border border-border shadow-lg"
      onclick={(e) => e.stopPropagation()}
    >
      <h2 class="text-2xl font-bold mb-4">{t.Title()}</h2>
      <p class="text-muted-foreground mb-6">{t.Message()}</p>

      {#if isClosing}
        <div class="flex flex-col items-center gap-4 py-4">
          <div class="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-muted-foreground"></div>
          <p class="text-sm text-muted-foreground">
            {stageMessages[shutdownStage] || t.Closing()}
          </p>
        </div>
      {:else}
        <div class="flex gap-3 justify-end">
          <button
            onclick={handleCancel}
            class="px-4 py-2 border border-border rounded hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            {t.Cancel()}
          </button>
          <button
            onclick={handleConfirm}
            class="px-4 py-2 bg-destructive text-destructive-foreground rounded hover:bg-destructive/90 transition-colors"
          >
            {t.Confirm()}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}
