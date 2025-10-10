<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { useTopic } from "$lib/ws";
  import PathBreadcrumb from "./PathBreadcrumb.svelte";
  import ResourceManager from "./ResourceManager.svelte";
  import type { FolderResponse } from "./types";

  const topicClient = useTopic<FolderResponse>("DevAssetsManager");
  const modRpc = topicClient.rpc();
  const pathRpc = topicClient.rpc();

  const mod_name = $derived(topicClient.data?.mod_name || "");
  const path = $derived(topicClient.data?.path || "");
  const mod_path_assets = $derived(topicClient.data?.mod_path_assets || "assets");

  function handleModChange(value: string) {
    modRpc.call("set_mod", { mod_name: value });
  }
  function handleNavigate(path: string) {
    pathRpc.call("set_path", { path: path });
  }
</script>

<div class="bg-background flex h-full w-full min-w-220 flex-col">
  <div class="bg-card border-border border-b px-4 pt-4 pb-3 shadow-sm">
    <div class="flex items-center gap-4">
      <div>
        <h1 class="text-foreground text-2xl font-bold">Asset Browser</h1>
      </div>
      <div class="w-64">
        <ModSelector {mod_name} handleEdit={handleModChange} />
      </div>
    </div>
  </div>
  <!-- Breadcrumb Navigation -->
  <PathBreadcrumb class="bg-card w-1/2" {mod_path_assets} {path} onNavigate={handleNavigate} />
  <div class="h-full min-h-160 w-1/2 flex-1 overflow-hidden">
    <ResourceManager class="h-1/2" {mod_name} folderData={topicClient.data} {path} onNavigate={handleNavigate} />
  </div>
</div>
