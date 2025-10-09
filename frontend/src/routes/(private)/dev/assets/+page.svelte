<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { useTopic } from "$lib/ws";
  import ResourceManager from "./ResourceManager.svelte";
  import type { FolderResponse } from "./types";

  const topicClient = useTopic<FolderResponse>("DevAssetsManager");
  const modRpc = topicClient.rpc();
  const pathRpc = topicClient.rpc();

  const mod_name = $derived(topicClient.data?.mod_name || "")
  const path = $derived(topicClient.data?.path || "")

  function handleModChange(value: string) {
    modRpc.call('set_mod', {"mod_name": value})
  }
  function handleNavigate(path: string) {
    pathRpc.call('set_path', {"path": path})
  }
</script>

<div class="bg-background flex h-screen w-full flex-col">
  <div class="bg-card border-border border-b px-6 py-4 shadow-sm">
    <div class="flex items-center gap-4">
      <div>
        <h1 class="text-foreground text-2xl font-bold">Asset Browser</h1>
      </div>
      <div class="w-64">
        <ModSelector {mod_name} handleEdit={handleModChange} />
      </div>
    </div>
  </div>

  <div class="flex-1 overflow-hidden">
    <ResourceManager {mod_name} folderData={topicClient.data} {path} onNavigate={handleNavigate} />
  </div>
</div>
