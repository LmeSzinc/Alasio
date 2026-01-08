<script lang="ts">
  import Arg from "$lib/components/arg/Arg.svelte";
  import ConfigItem from "$lib/components/aside/ConfigItem.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import type { ConfigLike, WORKER_STATUS } from "$lib/components/aside/types";
  import { useTopic } from "$lib/ws";
  import type { ConfigTopicLike } from "$lib/components/aside/types";
  import * as Card from "$lib/components/ui/card";

  // Subscribe to ConfigScan topic
  const topicClient = useTopic<ConfigTopicLike | undefined>("ConfigScan");

  // All available status options
  const ALL_STATUSES: WORKER_STATUS[] = [
    "idle",
    "starting",
    "running",
    "scheduler-stopping",
    "scheduler-waiting",
    "killing",
    "force-killing",
    "disconnected",
    "error",
  ];

  // Input state
  let configNameInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "config_name",
    dt: "input",
    value: "",
    name: "Config Name",
  });

  let activeInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "active",
    dt: "checkbox",
    value: false,
    name: "Active",
  });

  let statusInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "status",
    dt: "select",
    value: "idle",
    name: "Status",
    option: ALL_STATUSES,
  });

  let spinInput = $state<ArgData>({
    task: "",
    group: "",
    arg: "spin",
    dt: "checkbox",
    value: false,
    name: "Spin",
  });

  // Find config by name
  const selectedConfig = $derived.by(() => {
    const configName = configNameInput.value as string;
    if (!configName || !topicClient.data) return null;

    // Search for config by name
    for (const config of Object.values(topicClient.data)) {
      if (config.name === configName) {
        return config;
      }
    }
    return null;
  });

  // Create a mock config for demonstrations
  const mockConfig: ConfigLike = {
    id: 999,
    name: "Config",
    mod: "StarRail",
    gid: 0,
    iid: 0,
  };
</script>

<div class="container h-full w-full mx-auto flex flex-col gap-4 overflow-auto p-4">
  <h1 class="text-3xl font-bold">ConfigItem Debug Page</h1>

  <div class="grid gap-4 md:grid-cols-2">
    <!-- Part 2: Single Config Item Preview (Left) -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Selected Config Preview</Card.Title>
      </Card.Header>
      <Card.Content>
        {#if selectedConfig}
          <div class="flex items-start gap-4">
            <div class="w-20">
              <ConfigItem
                config={selectedConfig}
                status={statusInput.value as WORKER_STATUS}
                active={activeInput.value as boolean}
                afspin={spinInput.value as boolean}
              />
            </div>

            <div class="flex-1 space-y-2 text-sm">
              <div><strong>Name:</strong> {selectedConfig.name}</div>
              <div><strong>Mod:</strong> {selectedConfig.mod}</div>
              <div><strong>Status:</strong> {statusInput.value}</div>
              <div><strong>Active:</strong> {activeInput.value}</div>
              <div><strong>Group ID:</strong> {selectedConfig.gid}</div>
              <div><strong>Item ID:</strong> {selectedConfig.iid}</div>
            </div>
          </div>
        {:else}
          <div class="text-muted-foreground flex h-32 items-center justify-center text-sm">No config selected</div>
        {/if}
      </Card.Content>
    </Card.Root>

    <!-- Part 1: Input Controls (Right) -->
    <Card.Root class="neushadow border-none">
      <Card.Header>
        <Card.Title>Control Panel</Card.Title>
      </Card.Header>
      <Card.Content>
        <div class="space-y-3">
          <Arg bind:data={configNameInput} />
          <Arg bind:data={activeInput} />
          <Arg bind:data={spinInput} />
          <Arg bind:data={statusInput} />
        </div>

        {#if configNameInput.value && !selectedConfig}
          <div class="text-destructive mt-2 text-sm">
            Config "{configNameInput.value}" not found. Make sure the config exists in ConfigScan topic.
          </div>
        {/if}
      </Card.Content>
    </Card.Root>
  </div>

  <!-- Part 3: All Combinations Grid -->
  <Card.Root class="neushadow border-none">
    <Card.Header>
      <Card.Title>All Status × Active Combinations</Card.Title>
    </Card.Header>
    <Card.Content>
      <div class="space-y-2">
        <!-- Inactive -->
        <div>
          <h3 class="mb-2 text-lg font-medium">Inactive</h3>
          <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-9">
            {#each ALL_STATUSES as status}
              <div class="flex flex-col items-center gap-2 rounded border p-3">
                <div class="w-full truncate text-center font-mono text-xs" title={status}>
                  {status}
                </div>
                <ConfigItem config={selectedConfig || mockConfig} {status} active={false} afspin={false} />
              </div>
            {/each}
          </div>
        </div>

        <!-- Active -->
        <div>
          <h3 class="mb-2 text-lg font-medium">Active</h3>
          <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-9">
            {#each ALL_STATUSES as status}
              <div class="flex flex-col items-center gap-2 rounded border p-3">
                <div class="w-full truncate text-center font-mono text-xs" title={status}>
                  {status}
                </div>
                <ConfigItem config={selectedConfig || mockConfig} {status} active={true} afspin={false} />
              </div>
            {/each}
          </div>
        </div>

        <!-- Inactive + Spin -->
        <div>
          <h3 class="mb-2 text-lg font-medium">Inactive + Spin</h3>
          <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-9">
            {#each ALL_STATUSES as status}
              <div class="flex flex-col items-center gap-2 rounded border p-3">
                <div class="w-full truncate text-center font-mono text-xs" title={status}>
                  {status}
                </div>
                <ConfigItem config={selectedConfig || mockConfig} {status} active={false} afspin={true} />
              </div>
            {/each}
          </div>
        </div>

        <!-- Active + Spin -->
        <div>
          <h3 class="mb-2 text-lg font-medium">Active + Spin</h3>
          <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-9">
            {#each ALL_STATUSES as status}
              <div class="flex flex-col items-center gap-2 rounded border p-3">
                <div class="w-full truncate text-center font-mono text-xs" title={status}>
                  {status}
                </div>
                <ConfigItem config={selectedConfig || mockConfig} {status} active={true} afspin={true} />
              </div>
            {/each}
          </div>
        </div>
      </div>
    </Card.Content>
  </Card.Root>
</div>
