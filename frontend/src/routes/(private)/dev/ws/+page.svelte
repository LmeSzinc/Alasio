<script lang="ts">
  import { onDestroy } from "svelte";
  import { websocketClient } from "$lib/ws";
  import { createRpc } from "$lib/ws/rpc.svelte";
  import { Loader2 } from "@lucide/svelte";

  // Import UI components
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import { Badge } from "$lib/components/ui/badge";
  import Help from "$lib/components/ui/help/help.svelte";

  // --- Form State ---
  let topic = $state("ConfigScan");
  let func = $state("get_config");
  let args = $state('{"keys": ["alas.debug.print_exception"]}');

  // --- Client and Subscription State ---
  const topicsData = websocketClient.topics;

  // Directly access the reactive state from the websocketClient for the badge.
  const badgeVariant = $derived(
    websocketClient.connectionState === "open"
      ? "default"
      : websocketClient.connectionState === "closed"
        ? "destructive"
        : "secondary",
  );

  // --- RPC State ---
  // Use $derived to create a new RPC handler whenever the topic changes.
  // This ensures the RPC calls are always sent to the currently selected topic.
  const rpc = $derived(createRpc(topic, websocketClient));

  // --- Lifecycle and Effects ---
  $effect(() => {
    // Connect on component mount
    websocketClient.connect();
  });

  // Get default subscriptions for sorting
  const defaultSubscriptions = websocketClient.getDefaultSubscriptions();

  // Define a type alias for clarity
  type SortedTopicEntry = [string, any];

  // Use $state and $effect for sortedTopicsData to ensure explicit array type
  let sortedTopicsData: SortedTopicEntry[] = $state([]);

  $effect(() => {
    // Sort topics to show default subscriptions first
    const sorted: SortedTopicEntry[] = [];
    const nonDefault: SortedTopicEntry[] = [];

    // First, add default subscriptions
    for (const [topicName, data] of Object.entries(topicsData)) {
      if (defaultSubscriptions.includes(topicName)) {
        sorted.push([topicName, data]);
      } else {
        nonDefault.push([topicName, data]);
      }
    }

    // Then, add non-default subscriptions
    sorted.push(...nonDefault);

    sortedTopicsData = sorted; // Update the $state variable
  });

  function handleRpcSubmit() {
    if (!topic) {
      alert("Topic is required.");
      return;
    }
    if (!func) {
      alert("Function name is required for RPC.");
      return;
    }
    try {
      const parsedArgs = args ? JSON.parse(args) : {};
      rpc.call(func, parsedArgs);
    } catch (e) {
      alert("Invalid JSON in arguments field.");
      console.error(e);
    }
  }

  function handleSubscribe() {
    if (topic) websocketClient.sub(topic, true);
  }

  function handleUnsubscribe() {
    if (topic) websocketClient.unsub(topic, true);
  }

  onDestroy(websocketClient.unsubAll);
</script>

<div class="mx-auto p-2">
  <header class="mb-8 flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">WebSocket Test Client</h1>
  </header>

  <div class="grid grid-cols-1 gap-8 lg:grid-cols-2">
    <!-- Left Column: Command Panel -->
    <div class="space-y-6">
      <Card.Root class="relative">
        <Badge variant={badgeVariant} class="absolute top-4 right-4 capitalize transition-colors">
          {websocketClient.connectionState}
        </Badge>
        <Card.Header>
          <Card.Title>Send Command</Card.Title>
          <Card.Description>Manage topic subscriptions and send RPC calls.</Card.Description>
        </Card.Header>
        <Card.Content class="space-y-6">
          <!-- Topic Management -->
          <div class="grid gap-2">
            <Label for="topic">Topic</Label>
            <div class="flex items-center gap-2">
              <Input id="topic" class="flex-grow font-mono" bind:value={topic} placeholder="e.g., ConfigScan" />
              <Button variant="outline" onclick={handleSubscribe}>Sub</Button>
              <Button variant="outline" onclick={handleUnsubscribe}>Unsub</Button>
            </div>
          </div>

          <!-- RPC Inputs -->
          <div class="grid gap-2">
            <Label for="func">RPC Function</Label>
            <Input id="func" class="font-mono" bind:value={func} placeholder="e.g., get_config" />
          </div>
          <div class="grid gap-2">
            <Label for="args">RPC Arguments (JSON format)</Label>
            <textarea
              id="args"
              bind:value={args}
              class="border-input bg-background ring-offset-background focus-visible:ring-ring flex min-h-[80px] w-full rounded-md border px-3 py-2 font-mono text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              placeholder={'{"key": "value"}'}
            ></textarea>
          </div>
        </Card.Content>
        <Card.Footer>
          <div class="grid w-full gap-2">
            {#if rpc.isPending}
              <div class="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 class="h-4 w-4 animate-spin" />
                <span>Sending...</span>
              </div>
            {/if}
            {#if rpc.errorMsg}
              <Help variant="error">{rpc.errorMsg}</Help>
            {/if}
            {#if rpc.successMsg}
              <Help variant="default">Call succeeded. ID: {rpc.successMsg}</Help>
            {/if}
            <Button onclick={handleRpcSubmit}>RPC Call</Button>
          </div>
        </Card.Footer>
      </Card.Root>
    </div>

    <!-- Right Column: Subscriptions -->
    <div class="space-y-4">
      <h2 class="text-2xl font-semibold tracking-tight">Subscribed Topics Data</h2>
      {#if sortedTopicsData.length === 0}
        <div class="rounded-lg border-2 border-dashed py-10 text-center">
          <p class="text-muted-foreground">No data received yet.</p>
          <p class="text-muted-foreground text-sm">Subscribe to a topic to see its data here.</p>
        </div>
      {:else}
        <!-- This container arranges the cards -->
        <div class="space-y-4">
          {#each sortedTopicsData as [topicName, data]}
            <Card.Root class="flex max-h-[32rem] w-full flex-col">
              <Card.Header class="flex flex-row items-center justify-between">
                <Card.Title>{topicName}</Card.Title>
                {#if topicsData[topicName]}
                  <Badge variant="outline">Subscribed</Badge>
                {/if}
              </Card.Header>
              <Card.Content class="flex-grow overflow-auto">
                {#if typeof data === "object" && data !== null}
                  <pre class="h-full font-mono text-sm whitespace-pre-wrap select-text"><code
                      >{JSON.stringify(data, null, 2)}</code
                    ></pre>
                {:else}
                  <div class="font-mono text-sm whitespace-pre-wrap select-text">{data}</div>
                {/if}
              </Card.Content>
            </Card.Root>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>
