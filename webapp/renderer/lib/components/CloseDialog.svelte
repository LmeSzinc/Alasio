<script lang="ts">
  import { onMount } from "svelte";
  import * as AlertDialog from "$lib/components/ui/alert-dialog";
  import { buttonVariants } from "$lib/components/ui/button/button.svelte";
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

<AlertDialog.Root bind:open={show}>
  <AlertDialog.Content
    onEscapeKeydown={(e) => {
      if (isClosing) e.preventDefault();
    }}
  >
    <AlertDialog.Header>
      <AlertDialog.Title>{t.CloseDialog.Title()}</AlertDialog.Title>
      <AlertDialog.Description>{t.CloseDialog.Message()}</AlertDialog.Description>
    </AlertDialog.Header>

    {#if isClosing}
      <div class="flex flex-col items-center gap-4 py-4">
        <div class="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-muted-foreground"></div>
        <p class="text-sm text-muted-foreground">
          {stageMessages[shutdownStage] || t.CloseDialog.Closing()}
        </p>
      </div>
    {:else}
      <AlertDialog.Footer>
        <AlertDialog.Cancel onclick={handleCancel}>{t.CloseDialog.Cancel()}</AlertDialog.Cancel>
        <button onclick={handleConfirm} class={buttonVariants({ variant: "destructive" })}>
          {t.CloseDialog.Confirm()}
        </button>
      </AlertDialog.Footer>
    {/if}
  </AlertDialog.Content>
</AlertDialog.Root>
