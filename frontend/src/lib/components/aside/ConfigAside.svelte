<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";
  import { Settings } from "@lucide/svelte";
  import ConfigItem from "./ConfigItem.svelte";
  import type { ConfigLike, ConfigTopicLike, WORKER_STATUS } from "./types";

  // Subscribe to ConfigScan topic
  const topicClient = useTopic<ConfigTopicLike | undefined>("ConfigScan");
  const workerClient = useTopic<Record<string, WORKER_STATUS> | undefined>("Worker");

  // props
  type $$props = {
    class?: string;
    onNavigate?: () => void;
  };
  let { class: className, onNavigate = () => {} }: $$props = $props();

  // Easter egg spinning
  const afspin = $derived.by(() => {
    const now = new Date();
    return now.getMonth() === 3 && now.getDate() === 1;
  });

  // UI state
  type ConfigGroupData = {
    id: string;
    gid: number;
    items: ConfigLike[];
  };
  let groups = $state<ConfigGroupData[]>([]);

  // This effect syncs server data with UI state
  $effect(() => {
    const serverData = topicClient.data;

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

  // Get current config_name from URL
  const activeConfigName = $derived(page.params.config_name ? decodeURIComponent(page.params.config_name) : undefined);

  // Handle config item click
  async function handleConfigClick(config: ConfigLike) {
    // push to `/config/{config_name}`, target page will do rpc call
    const encodedConfigName = encodeURIComponent(config.name);
    await goto(`/config/${encodedConfigName}`);
    // additional callback
    onNavigate();
  }

  // Navigate to settings
  function handleSettings() {
    goto("/dev/config");
  }
</script>

<aside
  class={cn("flex h-full max-h-screen w-20 flex-col", className)}
  role="navigation"
  aria-label="Configuration sidebar"
>
  <ScrollArea class="min-h-0 flex-1">
    <div class="space-y-1 p-1" role="list">
      {#each groups as group (group.id)}
        {#if group.items.length === 1}
          <!-- Single item in group - display directly -->
          {@const item = group.items[0]}
          {@const active = activeConfigName === item.name}
          {@const status = workerClient.data?.[item.name] ?? "idle"}
          <div role="listitem" class="px-1">
            <ConfigItem config={item} {active} {status} {afspin} onclick={handleConfigClick} />
          </div>
        {:else if group.items.length > 1}
          <!-- Multiple items in group - display with border -->
          <div
            role="group"
            aria-label="Configuration group {group.gid}"
            class="border-foreground/20 relative rounded-lg border border-dashed p-1"
          >
            {#each group.items as item (item.id)}
              <!-- Items in vertical layout -->
              {@const active = activeConfigName === item.name}
              {@const status = workerClient.data?.[item.name] ?? "idle"}
              <div role="listitem">
                <ConfigItem config={item} {active} {status} {afspin} onclick={handleConfigClick} />
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
      class="hover:bg-accent/50 focus:ring-ring flex w-full cursor-pointer flex-col items-center justify-center rounded-md p-1 transition-colors focus:ring-2 focus:ring-offset-2 focus:outline-none"
      onclick={handleSettings}
      aria-label="Open configuration settings"
    >
      <Settings class="h-6 w-6" strokeWidth="1.5" aria-hidden="true" />
      <span class="mt-1 line-clamp-2 text-center text-xs break-all" aria-hidden="true"> Tools </span>
    </button>
  </div>
</aside>
