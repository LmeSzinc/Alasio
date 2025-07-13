<script lang="ts">
  import { DndProvider, type DndEndCallbackDetail } from "$lib/components/dnd";
  import { Badge } from "$lib/components/ui/badge";
  import { websocketClient } from "$lib/ws";
  import { arrayMove } from "@dnd-kit-svelte/sortable";
  import { Loader2 } from "@lucide/svelte";
  import ConfigGroup from "./ConfigGroup.svelte";
  import type { Config } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";

  // SINGLE SOURCE OF TRUTH (from server)
  const topicClient = websocketClient.sub("ConfigScan");
  const rpc = topicClient.rpc();

  type ConfigGroupData = {
    id: number;
    items: Config[];
  };

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
        return { id: gid, items };
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
  function handleDndEnd(detail: DndEndCallbackDetail) {
    const { active, over, position } = detail;
    const activeType = active.data?.type;
    const overType = over.data?.type;
    if (!activeType) return;

    // Group Drag Logic
    if (activeType === "group") {
      if (overType !== "group") return;
      const sourceIndex = uiGroups.findIndex((g) => g.id === active.id);
      const targetIndex = uiGroups.findIndex((g) => g.id === over.id);
      if (sourceIndex === -1 || targetIndex === -1) return;
      uiGroups = arrayMove(uiGroups, sourceIndex, targetIndex);
    }
    // Item Drag Logic
    else if (activeType === "item") {
      const sourceContainerId = active.data?.containerId as number;
      const sourceGroupIndex = uiGroups.findIndex((g) => g.id === sourceContainerId);
      if (sourceGroupIndex === -1) return;

      const targetContainerId = overType === "item" ? over.data?.containerId : over.id;
      const targetGroupIndex = uiGroups.findIndex((g) => g.id === targetContainerId);
      if (targetGroupIndex === -1) return;

      // FIX 1: Use `arrayMove` for the simple in-group reordering case.
      if (sourceGroupIndex === targetGroupIndex) {
        const sourceItemIndex = uiGroups[sourceGroupIndex].items.findIndex((i) => i.name === active.id);
        const targetItemIndex = uiGroups[targetGroupIndex].items.findIndex((i) => i.name === over.id);
        if (sourceItemIndex === -1 || targetItemIndex === -1) return;
        uiGroups[sourceGroupIndex].items = arrayMove(
          uiGroups[sourceGroupIndex].items,
          sourceItemIndex,
          targetItemIndex,
        );
      } else {
        // Move between groups
        const sourceItemIndex = uiGroups[sourceGroupIndex].items.findIndex((i) => i.name === active.id);
        if (sourceItemIndex === -1) return;
        const [movedItem] = uiGroups[sourceGroupIndex].items.splice(sourceItemIndex, 1);

        let targetItemIndex = -1;
        if (overType === "item") {
          targetItemIndex = uiGroups[targetGroupIndex].items.findIndex((i) => i.name === over.id);
          if (position === "after") targetItemIndex++;
        } else {
          // Dropped on a group container
          targetItemIndex = 0;
        }
        if (targetItemIndex === -1) return;
        uiGroups[targetGroupIndex].items.splice(targetItemIndex, 0, movedItem);

        if (uiGroups[sourceGroupIndex].items.length === 0) {
          uiGroups.splice(sourceGroupIndex, 1);
        }
      }
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
    <DndProvider onDndEnd={handleDndEnd}>
      {#snippet children({ dropIndicator, active })}
        <div class="space-y-2">
          {#each uiGroups as group (group.id)}
            <ConfigGroup
              {group}
              indicator={dropIndicator.targetId === group.id && active?.data?.type === "group"
                ? dropIndicator.position
                : null}
            >
              {#each group.items as config (config.name)}
                <ConfigItem
                  {config}
                  indicator={dropIndicator.targetId === config.name ? dropIndicator.position : null}
                />
              {/each}
            </ConfigGroup>
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
              <ConfigGroup group={active.data.group}>
                {#each active.data.group.items as config (config.name)}
                  <ConfigItem {config} />
                {/each}
              </ConfigGroup>
            </div>
          {/if}
        {/if}
      {/snippet}
    </DndProvider>
  {/if}
</div>
