<script lang="ts">
  import { onDestroy } from "svelte";
  import { websocketClient } from "$lib/ws";
  import { createRpc } from "$lib/ws/rpc.svelte";

  // Import UI components
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import * as ToggleGroup from "$lib/components/ui/toggle-group";
  import { Badge } from "$lib/components/ui/badge";
  import Help from "$lib/components/ui/help/help.svelte";

  // --- Form State ---
  let topic = $state("ConfigScan");
  let operation: "sub" | "unsub" | "rpc" = $state("sub");
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

  /**
   * Handles form submission to send commands via the WebSocket client.
   */
  function handleSubmit() {
    if (!topic) {
      alert("Topic is required.");
      return;
    }

    switch (operation) {
      case "sub":
        websocketClient.sub(topic, true);
        return;
      case "unsub":
        websocketClient.unsub(topic, true);
        return;
      case "rpc":
        if (!func) {
          alert("Function name is required for RPC.");
          return;
        }
        try {
          const parsedArgs = args ? JSON.parse(args) : {};
          rpc.send(func, parsedArgs);
        } catch (e) {
          alert("Invalid JSON in arguments field.");
          console.error(e);
        }
        return;
    }
  }

  // Clean up all subscriptions on component destruction
  onDestroy(websocketClient.unsubAll);
</script>

<div class="container mx-auto p-4 md:p-8">
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
          <Card.Description>Construct and send a message to the WebSocket server.</Card.Description>
        </Card.Header>
        <Card.Content class="space-y-6">
          <!-- Topic Input -->
          <div class="grid gap-2">
            <Label for="topic">Topic</Label>
            <Input id="topic" bind:value={topic} placeholder="e.g., ConfigScan or logs" />
          </div>

          <!-- Operation Toggle Group -->
          <div class="grid gap-2">
            <Label>Operation</Label>
            <ToggleGroup.Root type="single" bind:value={operation} variant="outline">
              {#each ["sub", "unsub", "rpc"] as op}
                <ToggleGroup.Item value={op} class="font-mono" aria-label={`Select operation ${op}`}>
                  {op}
                </ToggleGroup.Item>
              {/each}
            </ToggleGroup.Root>
          </div>

          <!-- RPC Inputs (Conditional) -->
          {#if operation === "rpc"}
              <div class="grid gap-2">
                <Label for="func">Function</Label>
                <Input id="func" bind:value={func} placeholder="e.g., get_config" />
              </div>
              <!-- Value Textarea (Conditional) -->
              <div class="grid gap-2">
                <Label for="args">Arguments (JSON format)</Label>
                <textarea
                  id="args"
                  bind:value={args}
                  class="border-input bg-background ring-offset-background focus-visible:ring-ring flex min-h-[80px] w-full rounded-md border px-3 py-2 font-mono text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
                  placeholder={'{"key": "value"}'}
                ></textarea>
              </div>
              {#if rpc.isPending}
                <div class="text-muted-foreground flex items-center gap-2 text-sm">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="24"
                    height="24"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    class="h-4 w-4 animate-spin"
                  >
                    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                  </svg>
                  <span>Sending...</span>
                </div>
              {/if}
              {#if rpc.errorMsg}
                <Help variant="error">{rpc.errorMsg}</Help>
              {/if}
              {#if rpc.successMsg}
                <Help variant="default">Call succeeded. ID: {rpc.successMsg}</Help>
              {/if}
          {/if}
        </Card.Content>
        <Card.Footer>
          <Button onclick={handleSubmit} class="w-full">
            {#if operation === "rpc"}
              Send RPC
            {:else}
              Update Subscription
            {/if}
          </Button>
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
