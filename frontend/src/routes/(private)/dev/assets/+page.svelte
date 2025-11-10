<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { useTopic } from "$lib/ws";
  import AssetManager from "./AssetManager.svelte";
  import ImageViewer from "./ImageViewer.svelte";
  import PathBreadcrumb from "./PathBreadcrumb.svelte";
  import ResourceManager from "./ResourceManager.svelte";
  import StatusBar from "./StatusBar.svelte";
  import type { FolderResponse } from "./types";

  // header context
  HeaderContext.use(header);

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
  const url =
    "https://raw.githubusercontent.com/LmeSzinc/StarRailCopilot/f1aea44bfe7e55595670d35adb6f7e731a8e523c/assets/share/base/popup/BUFF_FORGOTTEN_HALL.png";
</script>

{#snippet header()}
  <div class="flex w-full flex-1 items-center justify-center gap-4">
    <h1 class="text-lg">Asset Browser</h1>
    <ModSelector class="w-64" {mod_name} handleEdit={handleModChange} />
  </div>
{/snippet}

<div class="bg-background flex h-full min-h-160 w-full min-w-240 flex-col">
  <!-- Header -->
  <!-- <div class="bg-card border-border border-b px-4 pt-3 pb-2 shadow-sm"></div> -->

  <!-- Main Content Area: Changed to flex-col for top-bottom layout -->
  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- TOP ROW: ResourceManager + ImageViewer -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Top Left: ResourceManager -->
      <div class="border-border flex w-1/2 flex-col overflow-hidden border-r">
        <PathBreadcrumb class="bg-card flex-shrink-0" {mod_path_assets} {path} onNavigate={handleNavigate} />
        <ResourceManager class="flex-1 overflow-hidden" {mod_name} {path} {topicClient} />
        <StatusBar data={topicClient.data} class="bg-card flex-shrink-0" />
      </div>

      <!-- Top Right: ImageViewer -->
      <div class="flex flex-1 flex-col overflow-hidden">
        <ImageViewer src={url} class="h-full w-full" />
      </div>
    </div>

    <!-- BOTTOM ROW: AssetManager + Editor -->
    <div class="border-border flex flex-1 overflow-hidden border-t">
      <!-- Bottom Left: AssetManager -->
      <div class="border-border flex w-1/2 flex-col overflow-hidden border-x">
        <AssetManager class="flex-1" {mod_name} {path} {topicClient} />
      </div>

      <!-- Bottom Right: Editor -->
      <div class="flex-1 p-4">
        <!-- Added padding for visual clarity -->
        Editor
      </div>
    </div>
  </div>
</div>
