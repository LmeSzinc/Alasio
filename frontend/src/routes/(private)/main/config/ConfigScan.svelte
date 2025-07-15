<script lang="ts">
  import { DndProvider, applyDnd, type DndEndCallbackDetail } from "$lib/components/dnd";
  import { websocketClient } from "$lib/ws";
  import { Loader2 } from "@lucide/svelte";
  import type { ConfigGroupData } from "./ConfigGroup.svelte";
  import ConfigGroup from "./ConfigGroup.svelte";
  import type { Config } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";

  // SINGLE SOURCE OF TRUTH (from server)
  const topicClient = websocketClient.sub("ConfigScan");
  const rpc = topicClient.rpc();

  // UI state (what the user sees and manipulates).
  // Note that this variable deep copies topicClient.data, and might get dirty during optimistic update
  let uiGroups = $state<ConfigGroupData[]>([]);
  const dndRules = { group: ["item", "group"], item: ["item"] };

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
    // 1. Convert Map to array and sort by group id (gid).
    uiGroups = Array.from(groups.entries())
      .sort(([gidA], [gidB]) => gidA - gidB)
      // 2. Map over the sorted entries to create the final structure.
      // This `map` operation is where the deep copy happens.
      .map(([gid, items]) => {
        // Create a new, sorted, and deeply copied array of items.
        const copiedAndSortedItems = items
          .sort((a, b) => a.iid - b.iid)
          // For each item, create a new object (a shallow copy, which is sufficient here).
          .map((item) => ({ ...item }));

        // Return a new group object containing the copied items.
        return {
          id: `group-${gid}`,
          gid: gid,
          items: copiedAndSortedItems,
        };
      });
  });
  // A helper map to lookup the correct acvite object during optimistic update
  const itemMap = $derived.by(() => {
    const map = new Map<string | number, Config | ConfigGroupData>();
    for (const group of uiGroups) {
      map.set(group.id, group);
      for (const item of group.items) {
        map.set(item.id, item);
      }
    }
    return map;
  });

  /**
   * Re-indexes all groups and items in place based on their current order in the array.
   * `gid` and `iid` will be sequential, starting from 1.
   * @param groups The uiGroups array to modify directly.
   */
  function reindex() {
    // Using forEach for clarity, but a standard for loop works just as well.
    uiGroups.forEach((group, groupIndex) => {
      group.gid = groupIndex + 1;
      group.id = `group-${group.gid}`;

      // For each item within the group, assign a new iid.
      group.items.forEach((item, itemIndex) => {
        item.iid = itemIndex + 1;
      });
    });
  }

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

  /*
   * Handle DND events when dragging an item as a new group
   */
  function handleDndNewGroup(
    active: DndEndCallbackDetail["active"],
    over: DndEndCallbackDetail["over"],
    position: "top" | "bottom" | "left" | "right",
  ) {
    // 1. Get the item being dragged and the group it's being dropped on.

    // 2. Find and remove the item from its original group.
    let movedItem: Config | undefined;
    let sourceGroup: ConfigGroupData | undefined;

    for (const group of uiGroups) {
      const itemIndex = group.items.findIndex((item) => item.id === active.id);
      if (itemIndex !== -1) {
        sourceGroup = group;
        // Use splice to remove the item and get it back.
        [movedItem] = group.items.splice(itemIndex, 1);
        break;
      }
    }

    // If for some reason the item wasn't found, abort.
    if (!movedItem || !sourceGroup) return;

    // 3. If the source group is now empty, we will remove it later.
    // We do this after insertion to simplify index calculations.

    // 4. Create a new group for the moved item.
    const newGroup: ConfigGroupData = {
      // Use a unique ID for the new group for Svelte's keyed each block.
      // The gid will be properly reassigned by syncChangesToServer.
      id: `group-new-${movedItem.id}`,
      gid: 0, // Placeholder GID
      items: [movedItem],
    };

    // 5. Find the index of the group we dropped onto.
    // We must do this *before* potentially removing an empty source group
    // to get the correct insertion point.
    const targetIndex = uiGroups.findIndex((g) => g.id === over.id);
    if (targetIndex === -1) return; // Should not happen

    // 6. Insert the new group above or below the target group.
    const insertionIndex = position === "top" ? targetIndex : targetIndex + 1;
    uiGroups.splice(insertionIndex, 0, newGroup);

    // 7. Now, filter out any groups that have become empty.
    // This is safer than splicing them out earlier as it doesn't affect indices.
    uiGroups = uiGroups.filter((g) => g.items.length > 0);
  }

  /**
   * Performs optimistic UI updates by only moving elements within the `uiGroups` array.
   */
  function handleDndEnd({ active, over, position }: DndEndCallbackDetail) {
    const activeType = active.data?.type;
    const overType = over.data?.type;
    if (!activeType || !overType) return;

    if (active.data?.type === "item" && over.data?.type === "group") {
      // Dragging an item as a new group
      handleDndNewGroup(active, over, position);
    } else {
      // Dragging an item or a group
      applyDnd(uiGroups, active, over, position, "items");
      uiGroups = uiGroups.filter((g) => g.items.length > 0);
    }
    reindex();
    // syncChangesToServer();
  }
</script>

<div class="container mx-auto p-4 md:p-8">
  <header class="mb-6 flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">Configuration Manager</h1>
  </header>

  {#if !topicClient.data}
    <div class="flex h-64 items-center justify-center rounded-lg border-2 border-dashed">
      <Loader2 class="text-muted-foreground mr-2 h-8 w-8 animate-spin" />
      <p class="text-muted-foreground">Waiting for configuration data...</p>
    </div>
  {:else}
    <DndProvider onDndEnd={handleDndEnd} orientation="vertical" {dndRules}>
      {#snippet children({ dropIndicator })}
        <div class="space-y-2">
          {#each uiGroups as group (group.id)}
            <ConfigGroup {group} {dropIndicator} />
          {/each}
        </div>
      {/snippet}

      {#snippet dragOverlay({ active })}
        {#if active && active.data}
          {@const activeData = itemMap.get(active.id)}
          {@const activeType = active.data.type}
          {#if activeType === "item"}
            <div class="opacity-95 shadow-xl">
              <ConfigItem config={activeData as Config} />
            </div>
          {:else if activeType === "group"}
            <div class="opacity-95 shadow-2xl">
              <ConfigGroup group={activeData as ConfigGroupData} />
            </div>
          {/if}
        {/if}
      {/snippet}
    </DndProvider>
  {/if}
</div>
