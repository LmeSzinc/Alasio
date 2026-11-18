<script lang="ts">
  import { onMount } from "svelte";
  import ArrowDownToLine from "@lucide/svelte/icons/arrow-down-to-line";
  import Copy from "@lucide/svelte/icons/copy";
  import Minus from "@lucide/svelte/icons/minus";
  import Square from "@lucide/svelte/icons/square";
  import X from "@lucide/svelte/icons/x";
  import Button from "$lib/components/ui/button/button.svelte";
  import { t } from "$lib/i18n";
  import { CONTROLS_GAP, setControlsWidth } from "$lib/windowFrame.svelte";
  import CloseDialog from "./CloseDialog.svelte";

  let isMaximized = $state(false);
  let showCloseDialog = $state(false);
  let root: HTMLDivElement | null = $state(null);

  // The host window owns the maximize state. Read it once on mount (the
  // window may already be maximized before this component exists, for
  // example with --window=maximize) and then follow every maximize and
  // unmaximize, including the ones this button did not trigger (double
  // click on the drag region, window shortcuts). A locally toggled flag
  // would drift away from the real window state.
  onMount(() => {
    window.electronAPI.isWindowMaximized().then((maximized) => {
      isMaximized = maximized;
    });

    return window.electronAPI.onWindowMaximizedChange((maximized) => {
      isMaximized = maximized;
    });
  });

  function handleHide() {
    window.electronAPI.hideWindow();
  }

  function handleMinimize() {
    window.electronAPI.minimizeWindow();
  }

  function handleMaximize() {
    window.electronAPI.maximizeWindow();
  }

  function handleClose() {
    showCloseDialog = true;
  }

  // The frontend keeps its header clear of the window controls, so it needs
  // their reserve width: the group itself plus the same 6px gap on its left
  // that `pr-1.5` gives it on the right. Measured instead of hardcoded, so a
  // button size or gap change reaches the frontend without a matching constant
  // on the other side. The observer only fires on size changes of the group,
  // never while the window is being dragged or resized by itself.
  onMount(() => {
    const element = root;
    if (!element) return;

    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0) return;
      setControlsWidth(window.innerWidth - rect.left + CONTROLS_GAP);
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  });
</script>

<div bind:this={root} class="flex items-center gap-1 pr-1.5" style="-webkit-app-region: no-drag">
  <Button
    variant="ghost"
    size="icon"
    class="dark:hover:bg-muted"
    title={t.WindowControls.HideToTray()}
    onclick={handleHide}
  >
    <ArrowDownToLine class="h-4 w-4" strokeWidth={1.5} />
  </Button>
  <Button
    variant="ghost"
    size="icon"
    class="dark:hover:bg-muted"
    title={t.WindowControls.Minimize()}
    onclick={handleMinimize}
  >
    <Minus class="h-4 w-4" strokeWidth={1.5} />
  </Button>
  <Button
    variant="ghost"
    size="icon"
    class="dark:hover:bg-muted"
    title={isMaximized ? t.WindowControls.Restore() : t.WindowControls.Maximize()}
    onclick={handleMaximize}
  >
    {#if isMaximized}
      <Copy class="h-4 w-4" strokeWidth={1.5} />
    {:else}
      <Square class="h-4 w-4" strokeWidth={1.5} />
    {/if}
  </Button>
  <Button
    variant="ghost"
    size="icon"
    class="hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive dark:hover:text-destructive-foreground"
    title={t.WindowControls.Close()}
    onclick={handleClose}
  >
    <X class="h-4 w-4" strokeWidth={1.5} />
  </Button>
</div>

{#if showCloseDialog}
  <CloseDialog bind:show={showCloseDialog} />
{/if}
