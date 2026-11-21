<script lang="ts">
  import Loader2 from "@lucide/svelte/icons/loader-circle";
  import Plus from "@lucide/svelte/icons/plus";
  import type { ConfigTopicLike } from "$lib/components/aside/types";
  import { type DndEndCallbackDetail, DndProvider } from "$lib/components/dnd";
  import { Button } from "$lib/components/ui/button";
  import { Help } from "$lib/components/ui/help";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import type { ConfigGroupData } from "./ConfigGroup.svelte";
  import ConfigGroup from "./ConfigGroup.svelte";
  import type { Config } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";
  import DialogAdd from "./DialogAdd.svelte";
  import DialogCopy from "./DialogCopy.svelte";
  import DialogDel from "./DialogDel.svelte";
  import DialogRename from "./DialogRename.svelte";
  import { type DropTarget, applyOptimisticDrop, buildGroups, buildItemMap, planDndChanges } from "./configDnd";

  // props
  type $props = {
    class?: string;
  };
  const { class: className }: $props = $props();

  // SINGLE SOURCE OF TRUTH (from server)
  const topicClient = useTopic<ConfigTopicLike>("ConfigScan");
  const rpc = topicClient.rpc();

  // RPC handlers for dialogs
  const addRpc = topicClient.rpc();
  const copyRpc = topicClient.rpc();
  const renameRpc = topicClient.rpc();
  const delRpc = topicClient.rpc();
  const dndRpc = topicClient.rpc(); // Separate RPC for drag and drop operations

  // UI state (what the user sees and manipulates).
  // Note that this variable deep copies topicClient.data, and might get dirty during optimistic update
  let uiGroups = $state<ConfigGroupData[]>([]);
  const dndRules = { group: ["item", "group"], item: ["item"] };

  // State for dialog management
  let copySourceConfig = $state<Config | null>(null);
  let renameTargetConfig = $state<Config | null>(null);
  let deleteTargetConfig = $state<Config | null>(null);

  // This effect now correctly depends on the true reactive source: `websocketClient.topics`.
  // It runs whenever the server sends new data for 'ConfigScan', resetting the UI to the canonical state.
  let forceRefresh = $state(0);
  $effect(() => {
    forceRefresh;
    const serverData = topicClient.data as Record<string, Config> | undefined;
    uiGroups = buildGroups(serverData);
  });
  // A helper map to lookup the correct acvite object during optimistic update
  const itemMap = $derived.by(() => buildItemMap(uiGroups));

  /**
   * Handles the end of a drag and drop operation.
   *
   * The request is planned before the optimistic update, while the layout
   * still holds the positions the instructions are relative to. Both steps
   * live in configDnd.ts, where the transitions are covered by tests.
   */
  function handleDndEnd({ active, over, position }: DndEndCallbackDetail) {
    if (!active || !over) return;

    const drop: DropTarget = {
      activeId: active.id,
      activeType: active.data?.type as string | undefined,
      overId: over.id,
      overType: over.data?.type as string | undefined,
      position,
    };
    if (!drop.activeType || !drop.overType) return;
    if (drop.activeId === drop.overId) return;

    const changes = planDndChanges(drop, itemMap);
    if (changes.length > 0) {
      dndRpc.call("config_dnd", { configs: changes });
    }

    uiGroups = applyOptimisticDrop(uiGroups, drop);
  }

  // Dialog handlers
  function handleAddConfig() {
    addRpc.open();
  }
  function handleCopyConfig(config: Config) {
    copySourceConfig = config;
    copyRpc.open();
  }
  function handleRenameConfig(config: Config) {
    renameTargetConfig = config;
    renameRpc.open();
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

<div class={cn("h-full w-full overflow-auto", className)}>
  <header class="mx-4 mb-6 flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">{t.ConfigScan.ConfigManager()}</h1>
    <Button onclick={handleAddConfig} class="flex items-center gap-2">
      <Plus class="h-4 w-4" />
      {t.ConfigScan.AddConfig()}
    </Button>
  </header>

  {#if !topicClient.data}
    <div class="flex h-64 items-center justify-center rounded-lg border-2 border-dashed">
      <Loader2 class="mr-2 h-8 w-8 animate-spin text-muted-foreground" />
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
          {#each uiGroups as group, index (group.id)}
            <ConfigGroup
              {group}
              number={index + 1}
              {dropIndicator}
              onCopy={handleCopyConfig}
              onRename={handleRenameConfig}
              onDelete={handleDeleteConfig}
            />
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
  <DialogRename rpc={renameRpc} sourceConfig={renameTargetConfig} />
  <DialogDel rpc={delRpc} targetConfig={deleteTargetConfig} />
</div>
