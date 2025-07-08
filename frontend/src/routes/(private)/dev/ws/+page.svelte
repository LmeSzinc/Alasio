<!-- src/routes/ws-test/+page.svelte -->
<script lang="ts">
  import { onDestroy } from "svelte";
  import { websocketClient, type TopicClient } from "$lib/ws";

  // Import UI components from shadcn-svelte/bits-ui
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import * as ToggleGroup from "$lib/components/ui/toggle-group/index";
  import { Badge } from "$lib/components/ui/badge";

  // --- Form State (using Svelte 5 runes) ---
  let topic = $state("ConfigScan");
  let operation: "sub" | "unsub" | "add" | "set" | "del" = $state("sub");
  let keys = $state([""]);
  let value = $state('{"new_key": "new_value"}');

  // --- Client State & Subscription Management ---
  const topicsData = websocketClient.topics;
  let activeSubs = $state<Record<string, TopicClient>>({});

  // Directly access the reactive state from the websocketClient for the badge.
  const badgeVariant = $derived(
    websocketClient.connectionState === "open"
      ? "default"
      : websocketClient.connectionState === "closed"
        ? "destructive"
        : "secondary",
  );

  // For this test page, we want to initiate the connection as soon as it loads.
  // This does not affect the lazy-loading nature of the client in the rest of the app.
  $effect(() => {
    websocketClient.connect();
  });

  // Get default subscriptions for sorting
  const defaultSubscriptions = websocketClient.getDefaultSubscriptions();

  // Define a type alias for clarity
  type SortedTopicEntry = [string, any];

  // Use $state and $effect for sortedTopicsData to ensure explicit array type
  let sortedTopicsData: SortedTopicEntry[] = $state([]);

  $effect(() => {
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
   * An effect to declaratively manage the state of the key inputs.
   * It ensures there is always exactly one empty input at the end of the list.
   */
  $effect(() => {
    // 1. Filter out any empty keys that are not at the end.
    const nonEmptyKeys = keys.filter((k) => k.trim() !== "");

    // 2. Construct the desired state: all non-empty keys followed by one empty key.
    const desiredKeys = [...nonEmptyKeys, ""];

    // 3. Only update the state if it's different from the desired state.
    //    This check is crucial to prevent an infinite loop of updates.
    if (JSON.stringify(keys) !== JSON.stringify(desiredKeys)) {
      keys = desiredKeys;
    }
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
    }

    const finalKeys = keys
      .filter((k) => k.trim() !== "")
      .map((k) => {
        const num = parseInt(k, 10);
        return isNaN(num) || String(num) !== k ? k : num;
      });

    let finalValue: any;
    if (operation === "add" || operation === "set") {
      try {
        finalValue = JSON.parse(value);
      } catch (e) {
        alert("Invalid JSON in value field.");
        console.error(e);
        return;
      }
    }

    websocketClient.sendRaw({
      t: topic,
      o: operation,
      k: finalKeys,
      v: finalValue,
    });
  }

  /**
   * Clean up all active subscriptions when the component is destroyed.
   */
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
              {#each ["sub", "unsub", "add", "set", "del"] as op}
                <ToggleGroup.Item value={op} class="font-mono" aria-label={`Select operation ${op}`}>
                  {op}
                </ToggleGroup.Item>
              {/each}
            </ToggleGroup.Root>
          </div>

          <!-- Keys Input (Conditional) -->
          {#if ["add", "set", "del"].includes(operation)}
            <div class="grid gap-2">
              <Label>Keys (path to data, leave empty for root)</Label>
              <div class="space-y-2">
                {#each keys as key, i (i)}
                  <Input bind:value={keys[i]} placeholder={i === 0 ? "e.g., preferences" : "e.g., theme or 0"} />
                {/each}
              </div>
            </div>
          {/if}

          <!-- Value Textarea (Conditional) -->
          {#if ["add", "set"].includes(operation)}
            <div class="grid gap-2">
              <Label for="value">Value (JSON format)</Label>
              <textarea
                id="value"
                bind:value
                class="border-input bg-background ring-offset-background focus-visible:ring-ring flex min-h-[100px] w-full rounded-md border px-3 py-2 font-mono text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
                placeholder={'{"key": "value"'}
              ></textarea>
            </div>
          {/if}
        </Card.Content>
        <Card.Footer>
          <Button onclick={handleSubmit} class="w-full">Send Command</Button>
        </Card.Footer>
      </Card.Root>
    </div>

    <!-- Right Column: Subscriptions -->
    <div class="space-y-4">
      <h2 class="text-2xl font-semibold tracking-tight">Subscribed Topics Data</h2>

      {#if Object.keys(topicsData).length === 0}
        <!-- This message shows only when there is absolutely no topic data -->
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
