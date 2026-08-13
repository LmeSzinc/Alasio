<script lang="ts">
  import { Power } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardHeader, CardTitle } from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";
  import RestartDialog from "./RestartDialog.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const restartRpc = topicClient.rpc();
</script>

<!-- Restart Backend Card -->
<Card class="neushadow min-w-64 border-0">
  <CardHeader>
    <CardTitle class="flex items-center gap-2">
      <Power class="h-5 w-5" />
      {t.DevTool.RestartBackend()}
    </CardTitle>
  </CardHeader>
  <CardContent>
    <Button onclick={restartRpc.open} variant="destructive" class="w-full">
      <Power class="mr-2 h-4 w-4" />
      {t.DevTool.RestartBackend()}
    </Button>
  </CardContent>
</Card>

<!-- Dialog -->
<RestartDialog rpc={restartRpc} />
