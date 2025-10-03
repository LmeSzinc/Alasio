<script lang="ts">
  import { Folder, ChevronLeft } from "@lucide/svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import ResourceFolder from "./ResourceFolder.svelte";
  import ResourceFile from "./ResourceFile.svelte";
  import type { FolderResponse, ResourceItem } from "./types";

  let {
    mod_name,
    currentPath = $bindable(""),
    folderData,
    onNavigate,
  }: {
    mod_name: string
    currentPath: string;
    folderData?: FolderResponse;
    onNavigate?: (newPath: string) => void;
  } = $props();

  const folders = $derived(folderData?.folders || []);
  const resources = $derived(folderData?.resources || {});

  const resourceList = $derived<ResourceItem[]>(
    Object.entries(resources).map(([displayName, resourceRow]) => ({
      displayName,
      ...resourceRow,
    })),
  );

  function handleFolderClick(folderName: string): void {
    const newPath = currentPath ? `${currentPath}/${folderName}` : folderName;
    onNavigate?.(newPath);
  }

  function handleGoUp(): void {
    if (!currentPath) return;
    const parts = currentPath.split("/");
    parts.pop();
    const newPath = parts.join("/");
    onNavigate?.(newPath);
  }
</script>

<div class="bg-background flex h-full w-full flex-col">
  <!-- Navigation bar -->
  <div class="bg-card border-border flex items-center gap-3 border-b px-4 py-3">
    <Folder class="text-muted-foreground h-5 w-5" />

    {#if currentPath}
      <button
        onclick={handleGoUp}
        class={cn("flex items-center gap-1 px-3 py-1 text-sm", "bg-muted hover:bg-accent rounded transition-colors")}
      >
        <ChevronLeft class="h-4 w-4" />
        Back
      </button>
    {/if}

    <div class="text-foreground text-sm">
      <span class="font-medium">Path:</span>
      <span class="font-mono">{currentPath || "/"}</span>
    </div>
  </div>

  <!-- Main content area -->
  <ScrollArea class="flex-1">
    <div class="p-6">
      {#if folders.length === 0 && resourceList.length === 0}
        <div class="text-muted-foreground flex h-full items-center justify-center">
          <p>This folder is empty</p>
        </div>
      {:else}
        <div class="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {#each folders as folderName}
            <ResourceFolder name={folderName} onOpen={() => handleFolderClick(folderName)} />
          {/each}

          {#each resourceList as resource}
            <ResourceFile {mod_name} {resource} {currentPath} />
          {/each}
        </div>
      {/if}
    </div>
  </ScrollArea>

  <!-- Status bar -->
  <div class="bg-card border-border text-muted-foreground border-t px-4 py-2 text-xs">
    {folders.length} folder{folders.length !== 1 ? "s" : ""},
    {resourceList.length} resource{resourceList.length !== 1 ? "s" : ""}
  </div>
</div>
