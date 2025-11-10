<script lang="ts">
  import { DndProvider, applyDnd, type DndEndCallbackDetail } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { Help } from "$lib/components/ui/help";
  import { useTopic } from "$lib/ws";
  import { Loader2, Plus } from "@lucide/svelte";
  import type { ConfigGroupData } from "./ConfigGroup.svelte";
  import ConfigGroup from "./ConfigGroup.svelte";
  import type { Config } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";
  import DialogAdd from "./DialogAdd.svelte";
  import DialogCopy from "./DialogCopy.svelte";
  import DialogDel from "./DialogDel.svelte";

  // props
  type $$props = {
    class?: string;
  };
  const { class: className }: $$props = $props();

  // SINGLE SOURCE OF TRUTH (from server)
  const topicClient = useTopic("ConfigScan");
  const rpc = topicClient.rpc();

  // RPC handlers for dialogs
  const addRpc = topicClient.rpc();
  const copyRpc = topicClient.rpc();
  const delRpc = topicClient.rpc();
  const dndRpc = topicClient.rpc(); // Separate RPC for drag and drop operations

  // UI state (what the user sees and manipulates).
  // Note that this variable deep copies topicClient.data, and might get dirty during optimistic update
  let uiGroups = $state<ConfigGroupData[]>([]);
  const dndRules = { group: ["item", "group"], item: ["item"] };

  // State for dialog management
  let copySourceConfig = $state<Config | null>(null);
  let deleteTargetConfig = $state<Config | null>(null);

  // This effect now correctly depends on the true reactive source: `websocketClient.topics`.
  // It runs whenever the server sends new data for 'ConfigScan', resetting the UI to the canonical state.
  let forceRefresh = $state(0);
  $effect(() => {
    forceRefresh;
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
        item.gid = group.gid; // Update item's gid to match group
        item.iid = itemIndex + 1;
      });
    });
  }

  /**
   * Calculates the drag and drop request based on the active item and drop target.
   * Uses fractional gid/iid values as per backend ScanTable.config_dnd expectation.
   */
  function syncChangesToServer(
    active: DndEndCallbackDetail["active"],
    over: DndEndCallbackDetail["over"],
    position: "top" | "bottom" | "left" | "right",
  ) {
    if (!active.data || !over.data) return;

    const activeType = active.data.type;
    const overType = over.data.type;

    let changes: { name: string; gid: number; iid: number }[] = [];

    if (activeType === "item") {
      const activeConfig = active.data.config as Config;

      if (overType === "item") {
        // Item dropped on another item
        const overConfig = over.data.config as Config;

        if (position === "top") {
          // Insert before the target item
          changes.push({
            name: activeConfig.name,
            gid: overConfig.gid,
            iid: overConfig.iid - 0.01,
          });
        } else if (position === "bottom") {
          // Insert after the target item
          changes.push({
            name: activeConfig.name,
            gid: overConfig.gid,
            iid: overConfig.iid + 0.01,
          });
        }
      } else if (overType === "group") {
        // Item dropped on a group (creating new group)
        const overGroup = over.data.group as ConfigGroupData;

        if (position === "top") {
          // Create new group before the target group
          changes.push({
            name: activeConfig.name,
            gid: overGroup.gid - 0.01,
            iid: 1,
          });
        } else if (position === "bottom") {
          // Create new group after the target group
          changes.push({
            name: activeConfig.name,
            gid: overGroup.gid + 0.01,
            iid: 1,
          });
        }
      }
    } else if (activeType === "group" && overType === "group") {
      // Group dropped on another group
      const activeGroup = itemMap.get(active.id) as ConfigGroupData;
      const overGroup = itemMap.get(over.id) as ConfigGroupData;

      if (!activeGroup || !overGroup) return;
      if (activeGroup.id === overGroup.id) return; // Same group, no change needed

      let targetGid: number;
      if (position === "top") {
        targetGid = overGroup.gid - 0.01;
      } else {
        targetGid = overGroup.gid + 0.01;
      }

      // Move all items in the group
      activeGroup.items.forEach((item, index) => {
        changes.push({
          name: item.name,
          gid: targetGid,
          iid: index + 1,
        });
      });
    }

    if (changes.length > 0) {
      console.log("Sending RPC with changes:", changes);
      dndRpc.call("config_dnd", {"configs": changes});
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
      // The gid will be properly reassigned by reindex().
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
   * Handles the end of a drag and drop operation.
   * Performs optimistic UI updates first, then sends request to server.
   */
  function handleDndEnd({ active, over, position }: DndEndCallbackDetail) {
    if (!active || !over || !active.data || !over.data) return;

    const activeType = active.data?.type;
    const overType = over.data?.type;
    if (!activeType || !overType) return;

    // First, send the request to server based on ORIGINAL positions (before optimistic update)
    syncChangesToServer(active, over, position);

    // Then perform optimistic UI updates
    if (active.data?.type === "item" && over.data?.type === "group") {
      // Dragging an item as a new group
      handleDndNewGroup(active, over, position);
    } else {
      // Dragging an item or a group
      applyDnd(uiGroups, active, over, position, "items");
      uiGroups = uiGroups.filter((g) => g.items.length > 0);
    }

    // Re-index the UI to have clean, sequential gid/iid values
    reindex();
  }

  // Dialog handlers
  function handleAddConfig() {
    addRpc.open();
  }
  function handleCopyConfig(config: Config) {
    copySourceConfig = config;
    copyRpc.open();
  }
  function handleDeleteConfig(config: Config) {
    deleteTargetConfig = config;
    delRpc.open();
  }
  $effect(() => {
    if (dndRpc.errorMsg) {
      // Optimistic rollback when DND RPC fails
      forceRefresh++;
    }
  });
</script>

<div class={className}>
  <header class="my-6 mx-4 flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">Configuration Manager</h1>
    <Button onclick={handleAddConfig} class="flex items-center gap-2">
      <Plus class="h-4 w-4" />
      Add Configuration
    </Button>
  </header>

  {#if !topicClient.data}
    <div class="flex h-64 items-center justify-center rounded-lg border-2 border-dashed">
      <Loader2 class="text-muted-foreground mr-2 h-8 w-8 animate-spin" />
      <p class="text-muted-foreground">Waiting for configuration data...</p>
    </div>
  {:else}
    <!-- Display drag and drop errors at the top -->
    {#if dndRpc.errorMsg}
      <div class="mb-4">
        <Help variant="error">
          Drag and drop failed: {dndRpc.errorMsg}
        </Help>
      </div>
    {/if}

    <DndProvider onDndEnd={handleDndEnd} orientation="vertical" {dndRules}>
      {#snippet children({ dropIndicator })}
        <div class="space-y-1">
          {#each uiGroups as group (group.id)}
            <ConfigGroup {group} {dropIndicator} onCopy={handleCopyConfig} onDelete={handleDeleteConfig} />
          {/each}
        </div>
      {/snippet}

      {#snippet dragOverlay({ active })}
        {@const activeType = active?.data?.type}
        {@const activeData = active ? itemMap.get(active.id) : null}
        {#if activeType === "item"}
          <div class="opacity-95 shadow-xl">
            <ConfigItem config={activeData as Config} />
          </div>
        {:else if activeType === "group"}
          <div class="opacity-95 shadow-2xl">
            <ConfigGroup group={activeData as ConfigGroupData} />
          </div>
        {/if}
      {/snippet}
    </DndProvider>
  {/if}

  <!-- Dialogs -->
  <DialogAdd rpc={addRpc} />
  <DialogCopy rpc={copyRpc} sourceConfig={copySourceConfig} />
  <DialogDel rpc={delRpc} targetConfig={deleteTargetConfig} />
</div>
