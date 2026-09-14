<script lang="ts">
  import Power from "@lucide/svelte/icons/power";
  import Zap from "@lucide/svelte/icons/zap";
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";
  import RestartDialog from "./RestartDialog.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const gracefulRpc = topicClient.rpc();
  const forceRpc = topicClient.rpc();

  // Display this tool as an arg row
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
  <!-- The two buttons stack vertically and share the input column: the graceful
       restart sits on the name row, the forced restart on the help row, so the
       help text stays in the left column instead of running under the buttons
       (side by side buttons overflow the 200px input column in English) -->
  <LayoutHorizontalLike {data} class="gap-y-2">
    {#snippet InputSnippet()}
      <Button onclick={gracefulRpc.open} variant="destructive" class="w-full" title={t.DevTool.RestartBackendHelp()}>
        <Power class="mr-2 h-4 w-4" />
        {t.DevTool.RestartBackend()}
      </Button>
    {/snippet}
    {#snippet PlaceholderSnippet()}
      <Button onclick={forceRpc.open} variant="outline" class="w-full" title={t.DevTool.ForceRestartBackendHelp()}>
        <Zap class="mr-2 h-4 w-4" />
        {t.DevTool.ForceRestartBackend()}
      </Button>
    {/snippet}
  </LayoutHorizontalLike>
</div>

<!-- Dialogs -->
<RestartDialog rpc={gracefulRpc} kind="graceful" />
<RestartDialog rpc={forceRpc} kind="force" />
