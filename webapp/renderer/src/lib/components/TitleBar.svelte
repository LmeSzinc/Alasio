<script lang="ts">
  import { Minus, Square, X, Minimize2, Copy } from "@lucide/svelte";
  import CloseDialog from "./CloseDialog.svelte";

  let isMaximized = $state(false);
  let showCloseDialog = $state(false);

  function handleHide() {
    window.electronAPI.hideWindow();
  }

  function handleMinimize() {
    window.electronAPI.minimizeWindow();
  }

  function handleMaximize() {
    isMaximized = !isMaximized;
    window.electronAPI.maximizeWindow();
  }

  function handleClose() {
    showCloseDialog = true;
  }
</script>

<div class="flex h-8 select-none items-center bg-background border-b border-border z-100">
  <div class="flex-1 px-4 cursor-move" style="-webkit-app-region: drag">
    <span class="text-sm font-semibold">Alasio</span>
  </div>

  <div class="flex" style="-webkit-app-region: no-drag">
    <button
      onclick={handleHide}
      class="flex h-8 w-12 items-center justify-center hover:bg-accent hover:text-accent-foreground transition-colors"
      title="Hide to tray"
    >
      <Minimize2 size={16} />
    </button>
    <button
      onclick={handleMinimize}
      class="flex h-8 w-12 items-center justify-center hover:bg-accent hover:text-accent-foreground transition-colors"
      title="Minimize"
    >
      <Minus size={16} />
    </button>
    <button
      onclick={handleMaximize}
      class="flex h-8 w-12 items-center justify-center hover:bg-accent hover:text-accent-foreground transition-colors"
      title="Maximize"
    >
      {#if isMaximized}
        <Copy size={14} />
      {:else}
        <Square size={14} />
      {/if}
    </button>
    <button
      onclick={handleClose}
      class="flex h-8 w-12 items-center justify-center hover:bg-destructive hover:text-destructive-foreground transition-colors"
      title="Close"
    >
      <X size={16} />
    </button>
  </div>
</div>

{#if showCloseDialog}
  <CloseDialog bind:show={showCloseDialog} />
{/if}
