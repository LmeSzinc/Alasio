<script lang="ts">
  import { Power } from "@lucide/svelte";
  import LayoutHorizontalLike from "$lib/components/arg/LayoutHorizontalLike.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { Button } from "$lib/components/ui/button";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";
  import RestartDialog from "./RestartDialog.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const restartRpc = topicClient.rpc();

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
  <LayoutHorizontalLike {data}>
    {#snippet InputSnippet()}
      <Button onclick={restartRpc.open} variant="destructive" class="w-full">
        <Power class="mr-2 h-4 w-4" />
        {t.DevTool.RestartBackend()}
      </Button>
    {/snippet}
  </LayoutHorizontalLike>
</div>

<!-- Dialog -->
<RestartDialog rpc={restartRpc} />
