<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import Help from "$lib/components/ui/help/help.svelte";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import { t } from "$lib/i18n";
  import { websocketClient } from "$lib/ws";
  import { createRpc } from "$lib/ws/rpc.svelte";
  import { Loader2 } from "@lucide/svelte";
  import { onDestroy } from "svelte";

  // --- Form State ---
  let topic = $state("ConfigScan");
  let func = $state("get_config");
  let args = $state('{"keys": ["alas.debug.print_exception"]}');

  // --- Client and Subscription State ---
  const topicsData = websocketClient.topics;

  // Track subscriptions made by this component
  // Use array to allow duplicate subscriptions for testing
  const componentSubscriptions: string[] = $state([]);

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
  const sortedTopicsData: SortedTopicEntry[] = $derived.by(() => {
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
    return [...sorted, ...nonDefault];
  });

  function handleRpcSubmit() {
    if (!topic) {
      alert(t.WebsocketTest.TopicRequired());
      return;
    }
    if (!func) {
      alert(t.WebsocketTest.FunctionRequired());
      return;
    }
    try {
      const parsedArgs = args ? JSON.parse(args) : {};
      rpc.call(func, parsedArgs);
    } catch (e) {
      alert(t.WebsocketTest.InvalidJson());
      console.error(e);
    }
  }

  function handleSubscribe() {
    if (topic) {
      websocketClient.sub(topic, true);
      // Track this subscription (allow duplicates for testing)
      componentSubscriptions.push(topic);
    }
  }

  function handleUnsubscribe() {
    if (topic) {
      websocketClient.unsub(topic, true);
      // Remove one instance from tracked subscriptions
      const index = componentSubscriptions.indexOf(topic);
      if (index > -1) {
        componentSubscriptions.splice(index, 1);
      }
    }
  }

  // Clean up only this component's subscriptions on destroy
  onDestroy(() => {
    // Unsubscribe from all topics that this component subscribed to
    // This respects the count of subscriptions
    for (const subscribedTopic of componentSubscriptions) {
      websocketClient.unsub(subscribedTopic);
    }
    componentSubscriptions.length = 0;
  });
</script>

<div class="mx-auto flex h-full w-full flex-col gap-4 overflow-auto p-4">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.WebsocketTest.Title()}</h1>
  </header>

  <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
    <!-- Left Column: Command Panel -->
    <div class="gap-4">
      <Card.Root class="relative">
        <Badge variant={badgeVariant} class="absolute top-4 right-4 capitalize transition-colors">
          {websocketClient.connectionState}
        </Badge>
        <Card.Header>
          <Card.Title>{t.WebsocketTest.SendCommand()}</Card.Title>
          <Card.Description>{t.WebsocketTest.SendCommandDesc()}</Card.Description>
        </Card.Header>
        <Card.Content class="gap-4">
          <!-- Topic Management -->
          <div class="grid gap-2">
            <Label for="topic">{t.WebsocketTest.Topic()}</Label>
            <div class="flex items-center gap-2">
              <Input
                id="topic"
                class="grow font-mono"
                bind:value={topic}
                placeholder={t.WebsocketTest.TopicPlaceholder()}
              />
              <Button variant="outline" onclick={handleSubscribe}>{t.WebsocketTest.Subscribe()}</Button>
              <Button variant="outline" onclick={handleUnsubscribe}>{t.WebsocketTest.Unsubscribe()}</Button>
            </div>
          </div>

          <!-- RPC Inputs -->
          <div class="grid gap-2">
            <Label for="func">{t.WebsocketTest.RpcFunction()}</Label>
            <Input
              id="func"
              class="font-mono"
              bind:value={func}
              placeholder={t.WebsocketTest.RpcFunctionPlaceholder()}
            />
          </div>
          <div class="grid gap-2">
            <Label for="args">{t.WebsocketTest.RpcArguments()}</Label>
            <textarea
              id="args"
              bind:value={args}
              class="border-input bg-background ring-offset-background focus-visible:ring-ring flex min-h-[80px] w-full rounded-md border px-3 py-2 font-mono text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
              placeholder={t.WebsocketTest.RpcArgumentsPlaceholder()}
            ></textarea>
          </div>
        </Card.Content>
        <Card.Footer>
          <div class="grid w-full gap-2">
            {#if rpc.isPending}
              <div class="text-muted-foreground flex items-center gap-2 text-sm">
                <Loader2 class="h-4 w-4 animate-spin" />
                <span>{t.WebsocketTest.Sending()}</span>
              </div>
            {/if}
            {#if rpc.errorMsg}
              <Help variant="error">{rpc.errorMsg}</Help>
            {/if}
            {#if rpc.successMsg}
              <Help variant="default">{t.WebsocketTest.CallSucceeded()}: {rpc.successMsg}</Help>
            {/if}
            <Button onclick={handleRpcSubmit}>{t.WebsocketTest.RpcCall()}</Button>
          </div>
        </Card.Footer>
      </Card.Root>
    </div>

    <!-- Right Column: Subscriptions -->
    <div class="space-y-4">
      {#if sortedTopicsData.length === 0}
        <div class="rounded-lg border-2 border-dashed py-10 text-center">
          <p class="text-muted-foreground">{t.WebsocketTest.NoDataReceived()}</p>
          <p class="text-muted-foreground text-sm">{t.WebsocketTest.SubscribeToSeeData()}</p>
        </div>
      {:else}
        <!-- This container arranges the cards -->
        <div class="space-y-4">
          {#each sortedTopicsData as [topicName, data]}
            <Card.Root class="flex max-h-80 w-full flex-col">
              <Card.Header class="flex flex-row items-center justify-between">
                <Card.Title>{topicName}</Card.Title>
                <div class="flex items-center gap-2">
                  {#if topicsData[topicName]}
                    <Badge variant="outline">{t.WebsocketTest.Subscribed()}</Badge>
                  {/if}
                  {#if componentSubscriptions.includes(topicName)}
                    {@const componentCount = componentSubscriptions.filter((t) => t === topicName).length}
                    <Badge variant="secondary">
                      {t.WebsocketTest.This()} ({componentCount})
                    </Badge>
                  {/if}
                  {#if websocketClient.subscriptions[topicName]}
                    <Badge variant="default"
                      >{t.WebsocketTest.Count()}: {websocketClient.subscriptions[topicName]}</Badge
                    >
                  {/if}
                </div>
              </Card.Header>
              <Card.Content class="grow overflow-auto">
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
