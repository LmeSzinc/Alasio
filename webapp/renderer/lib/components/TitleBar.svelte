<script lang="ts">
  import WindowControls from "./WindowControls.svelte";

  let { floating = false } = $props();
</script>

{#if floating}
  <!-- Floating mode: the embedded web app provides its own header,
       only the transparent drag strip and window controls are overlaid. -->
  <div class="fixed top-0 left-0 right-0 z-100 flex h-12 select-none items-center">
    <div class="flex-1 self-stretch" style="-webkit-app-region: drag"></div>
    <WindowControls />
  </div>
{:else}
  <!-- Keep h-12 aligned with AppHeader bottom in the embedded web app -->
  <div class="flex h-12 select-none items-center bg-background border-b border-border z-100">
    <!-- self-stretch makes the drag region span the full 48px title bar height -->
    <div class="flex flex-1 items-center self-stretch px-4" style="-webkit-app-region: drag">
      <span class="text-sm font-semibold">Alasio</span>
    </div>
    <WindowControls />
  </div>
{/if}
