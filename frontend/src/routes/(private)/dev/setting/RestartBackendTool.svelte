<script lang="ts">
  import Power from "@lucide/svelte/icons/power";
  import X from "@lucide/svelte/icons/x";
  import Zap from "@lucide/svelte/icons/zap";
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import type { RestartTopicLike } from "$lib/components/aside/types";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import RestartDialog from "./RestartDialog.svelte";

  type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

  type Props = {
    /**
     * Restart phase override, e.g. for the dev page. Undefined reads the
     * Restart topic (`null` = no restart in progress).
     */
    phase?: RestartPhase | null;
  };
  let { phase: phaseOverride }: Props = $props();

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const gracefulRpc = topicClient.rpc();
  const forceRpc = topicClient.rpc();
  const cancelRpc = topicClient.rpc();

  // Restart topic: the graceful stop runs in two phases. 'stopping' is the
  // wait for the running configs (the restart is still cancellable), and
  // 'shutting-down' is the point of no return: every config stopped, the
  // resume list is frozen and the backend is on its way out.
  const restartClient = useTopic<RestartTopicLike>("Restart");
  const phase = $derived(phaseOverride === undefined ? (restartClient.data?.phase ?? null) : phaseOverride);
  const isStopping = $derived(phase === "stopping");
  const isShuttingDown = $derived(phase === "shutting-down");

  // Look of a button that cannot be clicked any more (the point of no return):
  // the shadcn default fades a disabled button out and drops its pointer events,
  // which reads as "the control is gone". It keeps the colour of the card it
  // sits on instead (no hover highlight either) and the not-allowed cursor is
  // what says that it is dead -- the cursor needs the pointer events back, a
  // button with none is never hovered
  const disabledClass =
    "disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-100 " +
    "disabled:bg-card dark:disabled:bg-card disabled:hover:bg-card dark:disabled:hover:bg-card";

  function handleCancelRestart() {
    // No dialog: the click aborts the wait, the backend keeps running. The
    // rpc clears the Restart topic, so the button turns back into the restart
    // one on its own (a stale page gets an explicit 'No restart in progress')
    cancelRpc.call("cancel_restart", {});
  }

  // Display this tool as an arg row: the help text of the tool itself, what the
  // button of the row does right now is its own tooltip
  // $derived so name/help follow the current display language
  const data = $derived.by<ArgData>(() => ({
    task: "SystemTool",
    group: "SystemTool",
    arg: "RestartBackend",
    dt: "static",
    value: null,
    name: t.DevTool.RestartBackend(),
    help: t.DevTool.RestartBackendHelp(),
  }));
</script>

<hr />
<div class="flex flex-col gap-y-1.5">
  <!-- The two buttons stack vertically and share the input column: the button
       of the name row comes first, the forced restart on the help row, so the
       help text stays in the left column instead of running under the buttons
       (side by side buttons overflow the 200px input column in English) -->
  <LayoutHorizontalLike {data} class="gap-y-2">
    {#snippet InputSnippet()}
      {#if isStopping}
        <!-- Waiting for the running configs to stop: the button aborts the
             restart (the backend keeps running, the configs it stopped stay
             stopped) instead of asking for one that would be refused -->
        <Button
          onclick={handleCancelRestart}
          variant="outline"
          class="w-full"
          title={t.DevTool.CancelRestartHelp()}
          disabled={cancelRpc.isPending}
        >
          <X class="mr-2 h-4 w-4" />
          {t.DevTool.CancelRestart()}
        </Button>
      {:else if isShuttingDown}
        <!-- The point of no return: the button is only the record of the phase,
             its own cursor says that it cannot be clicked -->
        <Button variant="outline" class={cn("w-full", disabledClass)} title={t.DevTool.CancelRestartTooLate()} disabled>
          <X class="mr-2 h-4 w-4" />
          {t.DevTool.CancelRestart()}
        </Button>
      {:else}
        <Button onclick={gracefulRpc.open} variant="destructive" class="w-full" title={t.DevTool.RestartBackendHelp()}>
          <Power class="mr-2 h-4 w-4" />
          {t.DevTool.RestartBackend()}
        </Button>
      {/if}
    {/snippet}
    {#snippet PlaceholderSnippet()}
      {#if isShuttingDown}
        <Button
          variant="outline"
          class={cn("w-full", disabledClass)}
          title={t.DevTool.ForceRestartBackendHelp()}
          disabled
        >
          <Zap class="mr-2 h-4 w-4" />
          {t.DevTool.ForceRestartBackend()}
        </Button>
      {:else}
        <Button onclick={forceRpc.open} variant="outline" class="w-full" title={t.DevTool.ForceRestartBackendHelp()}>
          <Zap class="mr-2 h-4 w-4" />
          {t.DevTool.ForceRestartBackend()}
        </Button>
      {/if}
    {/snippet}
  </LayoutHorizontalLike>
</div>

<!-- Dialogs -->
<RestartDialog rpc={gracefulRpc} kind="graceful" />
<RestartDialog rpc={forceRpc} kind="force" />
