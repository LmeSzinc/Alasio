<script lang="ts">
  import { goto } from "$app/navigation";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";
  import { Settings } from "@lucide/svelte";
  import ConfigItem from "./ConfigItem.svelte";
  import type { ConfigLike, ConfigTopicLike } from "./types";

  // Subscribe to ConfigScan topic
  const topicClient = useTopic("ConfigScan");
  const stateClient = useTopic("ConnState");
  const rpc = stateClient.rpc();

  // props
  type $$props = {
    activeId?: number;
    class?: string;
    onNavigate?: () => void;
  };
  let { activeId, class: className, onNavigate = () => {} }: $$props = $props();

  // UI state
  type ConfigGroupData = {
    id: string;
    gid: number;
    items: ConfigLike[];
  };
  let groups = $state<ConfigGroupData[]>([]);

  // This effect syncs server data with UI state
  $effect(() => {
    const serverData = topicClient.data as ConfigTopicLike | undefined;

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
  async function handleConfigClick(config: ConfigLike) {
    // Call RPC to set the config
    rpc.call("set_config", { name: config.name });
    // Navigate to dashboard
    onNavigate();
    await goto(`/config/${config.id}`);
  }

  // Navigate to settings
  function handleSettings() {
    goto("/main/config");
  }
</script>

<aside
  class={cn("bg-background flex h-full max-h-screen w-20 flex-col", className)}
  role="navigation"
  aria-label="Configuration sidebar"
>
  <ScrollArea class="min-h-0 flex-1">
    <div class="space-y-2 px-1 py-2" role="list">
      {#each groups as group (group.id)}
        {#if group.items.length === 1}
          <!-- Single item in group - display directly -->
          {@const item = group.items[0]}
          {@const variant = activeId && item.id === activeId ? "active" : "default"}
          <div role="listitem" class="px-1">
            <ConfigItem config={item} {variant} onclick={handleConfigClick} />
          </div>
        {:else if group.items.length > 1}
          <!-- Multiple items in group - display with border -->
          <div
            role="group"
            aria-label="Configuration group {group.gid}"
            class="border-border relative space-y-1 rounded-lg border border-dashed px-1"
          >
            {#each group.items as item (item.id)}
              <!-- Items in vertical layout -->
              {@const variant = activeId && item.id === activeId ? "active" : "default"}
              <div role="listitem">
                <ConfigItem config={item} {variant} onclick={handleConfigClick} />
              </div>
            {/each}
          </div>
        {/if}
      {/each}
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
