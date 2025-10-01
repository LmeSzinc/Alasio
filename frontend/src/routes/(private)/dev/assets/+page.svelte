<script lang="ts">
  import { useTopic } from "$lib/ws";
  import ResourceManager from "./ResourceManager.svelte";
  import type { FolderResponse } from "./types";

  let currentPath = $state("assets/combat");
  const topicClient = useTopic<FolderResponse>("DevAssetsManager");

  function handleNavigate(newPath: string): void {
    currentPath = newPath;
    // loadFolderData(newPath);
  }
</script>

<div class="bg-background flex h-screen w-full flex-col">
  <div class="bg-card border-border border-b px-6 py-4 shadow-sm">
    <h1 class="text-foreground text-2xl font-bold">Asset Browser</h1>
    <p class="text-muted-foreground mt-1 text-sm">Browse and manage your game assets</p>
  </div>

  <div class="flex-1 overflow-hidden">
    <ResourceManager folderData={topicClient.data} bind:currentPath onNavigate={handleNavigate} />
  </div>
</div>
