<script lang="ts">
  import { goto } from "$app/navigation";
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "$lib/components/ui/card";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";
  import { LogOut, Power } from "@lucide/svelte";
  import RestartDialog from "./RestartDialog.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const restartRpc = topicClient.rpc();
</script>

<div class="container mx-auto p-6">
  <header class="mb-8">
    <h1 class="text-3xl font-bold tracking-tight">{t.DevTool.SystemTool()}</h1>
  </header>

  <div class="flex gap-4">
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

    <!-- Return to Login Card -->
    <Card class="neushadow min-w-64 border-0">
      <CardHeader>
        <CardTitle class="flex items-center gap-2">
          <LogOut class="h-5 w-5" />
          {t.DevTool.ReturnToLogin()}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Button onclick={() => goto("/auth")} variant="outline" class="w-full">
          <LogOut class="mr-2 h-4 w-4" />
          {t.DevTool.ReturnToLogin()}
        </Button>
      </CardContent>
    </Card>
  </div>
</div>

<!-- Dialog -->
<RestartDialog rpc={restartRpc} />
