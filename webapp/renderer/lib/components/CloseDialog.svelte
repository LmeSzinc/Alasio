<script lang="ts">
  import { onMount } from "svelte";
  import { buttonVariants } from "$lib/components/ui/button/button.svelte";
  import * as Dialog from "$lib/components/ui/dialog";
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

  // Dismissing a plain dialog (outside click, escape, close button) closes it
  // without asking: allowed while the confirmation is still up, refused once
  // the shutdown started, because the dialog then only reports its progress.
  function keepOpenWhileClosing(event: Event) {
    if (isClosing) event.preventDefault();
  }
</script>

<Dialog.Root bind:open={show}>
  <Dialog.Content
    showCloseButton={!isClosing}
    onEscapeKeydown={keepOpenWhileClosing}
    onInteractOutside={keepOpenWhileClosing}
  >
    <Dialog.Header>
      <Dialog.Title>{t.CloseDialog.Title()}</Dialog.Title>
      <Dialog.Description>{t.CloseDialog.Message()}</Dialog.Description>
    </Dialog.Header>

    {#if isClosing}
      <div class="flex flex-col items-center gap-4 py-4">
        <div class="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-muted-foreground"></div>
        <p class="text-sm text-muted-foreground">
          {stageMessages[shutdownStage] || t.CloseDialog.Closing()}
        </p>
      </div>
    {:else}
      <Dialog.Footer>
        <button onclick={handleCancel} class={buttonVariants({ variant: "outline" })}>
          {t.CloseDialog.Cancel()}
        </button>
        <button onclick={handleConfirm} class={buttonVariants({ variant: "destructive" })}>
          {t.CloseDialog.Confirm()}
        </button>
      </Dialog.Footer>
    {/if}
  </Dialog.Content>
</Dialog.Root>
