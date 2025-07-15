<script lang="ts">
  import { goto } from "$app/navigation";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { websocketClient } from "$lib/ws";
  import { Settings } from "@lucide/svelte";
  import { onDestroy } from "svelte";
  import type { ConfigLike as BasicConfigLike } from "./ConfigItem.svelte";
  import ConfigItem from "./ConfigItem.svelte";

  // Minimal type requirements
  type ConfigLike = {
    name: string;
    mod: string;
    gid: number;
    iid: number;
    [key: string]: any; // Allow any other properties
  };

  type ConfigGroupData = {
    id: string;
    gid: number;
    items: ConfigLike[];
  };

  // Subscribe to ConfigScan topic
  const topicClient = websocketClient.sub("ConfigScan");
  const rpc = topicClient.rpc();
  onDestroy(topicClient.unsub);

  // UI state
  let groups = $state<ConfigGroupData[]>([]);

  // This effect syncs server data with UI state
  $effect(() => {
    const serverData = topicClient.data as Record<string, ConfigLike> | undefined;

    if (!serverData) {
      groups = [];
      return;
    }

    // Group configs by gid
    const groupMap = new Map<number, ConfigLike[]>();
    for (const config of Object.values(serverData)) {
      if (!groupMap.has(config.gid)) groupMap.set(config.gid, []);
      groupMap.get(config.gid)!.push(config);
    }

    // Convert to array and sort
    groups = Array.from(groupMap.entries())
      .sort(([gidA], [gidB]) => gidA - gidB)
      .map(([gid, items]) => ({
        id: `group-${gid}`,
        gid: gid,
        items: items.sort((a, b) => a.iid - b.iid),
      }));
  });

  // Handle config item click
  async function handleConfigClick(config: BasicConfigLike) {
    // Call RPC to set the config
    rpc.call("set_current_config", { name: config.name });
    // Navigate to dashboard
    await goto("/config/dashboard");
  }

  // Navigate to settings
  function handleSettings() {
    goto("/main/config");
  }
</script>

<aside
  class="bg-card border-border flex h-full w-20 flex-col border-r absolute"
  role="navigation"
  aria-label="Configuration sidebar"
>
  <ScrollArea class="flex-1">
    <div class="px-1 py-2" role="list">
      <div class="space-y-2">
        {#each groups as group (group.id)}
          {#if group.items.length === 1}
            <!-- Single item in group - display directly -->
            <div role="listitem" class="px-1">
              <ConfigItem config={group.items[0]} onclick={handleConfigClick} />
            </div>
          {:else if group.items.length > 1}
            <!-- Multiple items in group - display with border -->
            <div
              role="group"
              aria-label="Configuration group {group.gid}"
              class="border-border relative rounded-lg border border-dashed px-1"
            >
              <!-- Items in vertical layout -->
              <div class="space-y-1" role="list">
                {#each group.items as item (item.id)}
                  <div role="listitem">
                    <ConfigItem config={item} onclick={handleConfigClick} />
                  </div>
                {/each}
              </div>
            </div>
          {/if}
        {/each}
      </div>
    </div>
  </ScrollArea>

  <!-- Settings button at the bottom -->
  <div class="border-border border-t p-2">
    <button
      class="hover:bg-accent/50 focus:ring-ring flex w-full cursor-pointer items-center justify-center rounded-md p-3 transition-colors focus:ring-2 focus:ring-offset-2 focus:outline-none"
      onclick={handleSettings}
      aria-label="Open configuration settings"
    >
      <Settings class="text-muted-foreground h-5 w-5" aria-hidden="true" />
    </button>
  </div>
</aside>
