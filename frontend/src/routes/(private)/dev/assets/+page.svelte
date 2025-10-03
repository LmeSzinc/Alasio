<script lang="ts">
  import ModSelector from "$lib/components/arginput/ModSelector.svelte";
  import { useTopic } from "$lib/ws";
  import ResourceManager from "./ResourceManager.svelte";
  import type { FolderResponse } from "./types";

  let mod_name = $state("");
  let currentPath = $state("assets/combat");

  const topicClient = useTopic<FolderResponse>("DevAssetsManager");

  function handleNavigate(newPath: string): void {
    currentPath = newPath;
  }
</script>

<div class="bg-background flex h-screen w-full flex-col">
  <div class="bg-card border-border border-b px-6 py-4 shadow-sm">
    <div class="flex items-center gap-4">
      <div>
        <h1 class="text-foreground text-2xl font-bold">Asset Browser</h1>
      </div>
      <div class="w-64">
        <ModSelector bind:mod_name selectFirst />
      </div>
    </div>
  </div>

  <div class="flex-1 overflow-hidden">
    <ResourceManager {mod_name} folderData={topicClient.data} bind:currentPath onNavigate={handleNavigate} />
  </div>
</div>
