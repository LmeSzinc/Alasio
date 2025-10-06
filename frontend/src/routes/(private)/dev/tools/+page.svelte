<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "$lib/components/ui/card";
  import { useTopic } from "$lib/ws";
  import { Power } from "@lucide/svelte";
  import Restart from "./Restart.svelte";

  // Connect to backend topic
  const topicClient = useTopic("ConnState");
  const restartRpc = topicClient.rpc();
</script>

<div class="container mx-auto p-6">
  <header class="mb-8">
    <h1 class="text-3xl font-bold tracking-tight">System Tools</h1>
    <p class="text-muted-foreground mt-2">Manage system operations and settings</p>
  </header>

  <div class="flex gap-4">
    <!-- Restart Backend Card -->
    <Card class="neushadow w-64 border-0">
      <CardHeader>
        <CardTitle class="flex items-center gap-2">
          <Power class="h-5 w-5" />
          Restart Backend
        </CardTitle>
      </CardHeader>
      <CardContent>
        <Button onclick={restartRpc.open} variant="destructive" class="w-full">
          <Power class="mr-2 h-4 w-4" />
          Restart Backend
        </Button>
      </CardContent>
    </Card>

    <!-- Placeholder for future tools -->
    <Card class="neushadow w-64 border-0">
      <CardHeader>
        <CardTitle>More Tools Coming Soon</CardTitle>
        <CardDescription>Additional system tools will be added here</CardDescription>
      </CardHeader>
      <CardContent>
        <p class="text-muted-foreground text-sm"></p>
      </CardContent>
    </Card>
  </div>
</div>

<!-- Dialog -->
<Restart rpc={restartRpc} />
