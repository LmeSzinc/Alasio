<script lang="ts">
  import { DndProvider, applyDnd, type DndEndCallbackDetail } from "$lib/components/dnd";
  import { Badge } from "$lib/components/ui/badge";
  import { websocketClient } from "$lib/ws";
  import { Loader2 } from "@lucide/svelte";
  import type { ConfigGroupData } from "./ConfigGroup.svelte";
  import ConfigGroup from "./ConfigGroup.svelte";
  import type { Config } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";

  // SINGLE SOURCE OF TRUTH (from server)
  const topicClient = websocketClient.sub("ConfigScan");
  const rpc = topicClient.rpc();

  // UI state (what the user sees and manipulates). It's a mutable $state variable.
  let uiGroups = $state<ConfigGroupData[]>([]);

  // This effect now correctly depends on the true reactive source: `websocketClient.topics`.
  // It runs whenever the server sends new data for 'ConfigScan', resetting the UI to the canonical state.
  $effect(() => {
    const serverData = topicClient.data as Record<string, Config> | undefined;

    if (!serverData) {
      uiGroups = [];
      return;
    }

    const groups = new Map<number, Config[]>();
    for (const config of Object.values(serverData)) {
      if (!groups.has(config.gid)) groups.set(config.gid, []);
      groups.get(config.gid)!.push(config);
    }
    const sortedGroups: ConfigGroupData[] = Array.from(groups.entries())
      .sort(([gidA], [gidB]) => gidA - gidB)
      .map(([gid, items]) => {
        items.sort((a, b) => a.iid - b.iid);
        // ConfigItem will have id=row.id as integer
        // ConfigGroup will have id=`group-${gid}` as string
        // so they are unique within uiGroups
        return { id: `group-${gid}`, gid, items };
      });

    // By assigning here, we sync our mutable UI state with the server's truth.
    uiGroups = sortedGroups;
  });

  /**
   * Compares the optimistically updated UI state against the last known server state
   * to generate a diff and send it via RPC.
   */
  function syncChangesToServer() {
    // The last known server state is our single source of truth for comparison.
    const lastServerData = topicClient.data as Record<string, Config> | undefined;
    if (!lastServerData) return;

    const changes: { name: string; gid: number; iid: number }[] = [];

    for (let newGid = 0; newGid < uiGroups.length; newGid++) {
      const group = uiGroups[newGid];
      for (let newIid = 0; newIid < group.items.length; newIid++) {
        const item = group.items[newIid];
        const originalItem = lastServerData[item.name];

        if (!originalItem) continue;

        if (originalItem.gid !== newGid || originalItem.iid !== newIid) {
          changes.push({ name: item.name, gid: newGid, iid: newIid });
        }
      }
    }

    if (changes.length > 0) {
      console.log("Sending RPC with changes:", changes);
      rpc.call("group_dnd", { configs: changes });
    }
  }

  /**
   * Performs optimistic UI updates by only moving elements within the `uiGroups` array.
   */
  function handleDndEnd({ active, over, position }: DndEndCallbackDetail) {
    const activeType = active.data?.type;
    const overType = over.data?.type;
    if (!activeType || !overType) return;

    // Group Drag Logic
    if (active.data?.type === "item" && over.data?.type === "group") {
      //
    } else {
      applyDnd(uiGroups, active, over, position, "items");
    }
    syncChangesToServer();
  }
</script>

<div class="container mx-auto p-4 md:p-8">
  <header class="mb-6 flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">Configuration Manager</h1>
    <Badge variant="outline">{websocketClient.connectionState}</Badge>
  </header>

  {#if !topicClient.data}
    <div class="flex h-64 items-center justify-center rounded-lg border-2 border-dashed">
      <Loader2 class="text-muted-foreground mr-2 h-8 w-8 animate-spin" />
      <p class="text-muted-foreground">Waiting for configuration data...</p>
    </div>
  {:else}
    <DndProvider onDndEnd={handleDndEnd} orientation="vertical">
      {#snippet children({ dropIndicator })}
        <div class="space-y-2">
          {#each uiGroups as group (group.id)}
            <ConfigGroup {group} {dropIndicator} />
          {/each}
        </div>
      {/snippet}

      {#snippet dragOverlay({ active })}
        {#if active && active.data}
          {@const activeType = active.data.type}
          {#if activeType === "item"}
            <div class="opacity-95 shadow-xl">
              <ConfigItem config={active.data.config} />
            </div>
          {:else if activeType === "group"}
            <div class="w-full opacity-95 shadow-2xl">
              <ConfigGroup group={active.data.group} />
            </div>
          {/if}
        {/if}
      {/snippet}
    </DndProvider>
  {/if}
</div>
