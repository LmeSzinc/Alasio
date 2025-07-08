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
  const connectionState = websocketClient.connectionState;
  const topicsData = websocketClient.topics;
  let activeSubs = $state<Record<string, TopicClient>>({});

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
        websocketClient.sub(topic);
        return;
      case "unsub":
        websocketClient.unsub(topic);
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

<div class="container mx-auto space-y-8 p-4 md:p-8">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">WebSocket Test Client</h1>
    <Badge
      variant={connectionState === "open" ? "default" : connectionState === "closed" ? "destructive" : "secondary"}
      class="capitalize transition-colors"
    >
      {connectionState}
    </Badge>
  </header>

  <!-- Command Panel -->
  <Card.Root>
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
        <ToggleGroup.Root type="single" bind:value={operation} class="flex flex-wrap items-center gap-1">
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
              <Input
                bind:value={keys[i]}
                placeholder={i === 0 ? "e.g., preferences" : "e.g., theme or 0"}
              />
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
            placeholder={'{"key": "value"}'}
          ></textarea>
        </div>
      {/if}
    </Card.Content>
    <Card.Footer>
      <Button onclick={handleSubmit} class="w-full">Send Command</Button>
    </Card.Footer>
  </Card.Root>

  <div class="space-y-4">
    <h2 class="text-2xl font-semibold tracking-tight">Subscribed Topics Data</h2>

    {#if Object.keys(topicsData).length === 0}
      <!-- This message shows only when there is absolutely no topic data -->
      <div class="rounded-lg border-2 border-dashed py-10 text-center">
        <p class="text-muted-foreground">No data received yet.</p>
        <p class="text-muted-foreground text-sm">Subscribe to a topic to see its data here.</p>
      </div>
    {:else}
      <!-- This container uses flexbox to arrange the cards -->
      <div class="flex flex-wrap gap-4">
        {#each Object.entries(topicsData) as [topicName, data]}
          <!-- 
						Each card is a flex item with max dimensions.
						It's also a column-based flex container itself to manage its internal layout.
					-->
          <Card.Root class="flex max-h-96 w-full max-w-lg flex-col">
            <Card.Header class="flex flex-row items-center justify-between">
              <Card.Title>{topicName}</Card.Title>
              {#if activeSubs[topicName]}
                <Badge variant="outline">Subscribed</Badge>
              {/if}
            </Card.Header>

            <!-- 
							The content area grows to fill available space and handles scrolling.
						-->
            <Card.Content class="flex-grow overflow-auto">
              <!-- 
								The <pre> tag allows text selection and is styled for code.
								It takes the full height of its scrolling parent.
							-->
              <pre class="h-full text-sm select-text"><code>{JSON.stringify(data, null, 2)}</code></pre>
            </Card.Content>
          </Card.Root>
        {/each}
      </div>
    {/if}
  </div>
</div>
