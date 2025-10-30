<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { FileZone, UploadProgress, UploadState } from "$lib/components/upload";
  import { cn } from "$lib/utils";
  import type { TopicLifespan } from "$lib/ws";
  import ResourceFile from "./ResourceFile.svelte";
  import ResourceFolder from "./ResourceFolder.svelte";
  import { selectionState } from "./selectionState.svelte";
  import type { FolderResponse, ResourceItem } from "./types";

  let {
    mod_name,
    path,
    topicClient,
    uploadState,
    class: className,
  }: {
    mod_name: string;
    path: string;
    topicClient: TopicLifespan<FolderResponse>;
    uploadState?: UploadState;
    class?: string;
  } = $props();

  const pathRpc = topicClient.rpc();
  function onNavigate(path: string) {
    pathRpc.call("set_path", { path: path });
  }

  const folders = $derived(topicClient.data?.folders || []);
  const resources = $derived(topicClient.data?.resources || {});

  const resourceList = $derived<ResourceItem[]>(
    Object.entries(resources).map(([displayName, resourceRow]) => ({
      displayName,
      ...resourceRow,
    })),
  );

  // Create a flat list of all items (folders + resources) for range selection
  const allItems = $derived([
    ...folders.map((name) => ({ type: "folder" as const, name })),
    ...resourceList.map((r) => ({ type: "resource" as const, name: r.name })),
  ]);

  function handleFolderSelect(folderName: string, event: MouseEvent): void {
    const item = { type: "folder" as const, name: folderName };

    if (event.shiftKey) {
      // Shift + Click: range selection
      selectionState.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      // Ctrl/Cmd + Click: toggle selection
      selectionState.toggle(item);
    } else {
      // Regular click: replace selection
      selectionState.select(item);
    }
  }

  function handleFolderOpen(folderName: string): void {
    const newPath = path ? `${path}/${folderName}` : folderName;
    onNavigate?.(newPath);
  }

  function handleResourceSelect(resource: ResourceItem, event: MouseEvent): void {
    const item = { type: "resource" as const, name: resource.name };

    if (event.shiftKey) {
      // Shift + Click: range selection
      selectionState.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      // Ctrl/Cmd + Click: toggle selection
      selectionState.toggle(item);
    } else {
      // Regular click: replace selection
      selectionState.select(item);
    }
  }

  function handleResourceOpen(resource: ResourceItem): void {
    // TODO: Implement resource open logic
    console.log("Open resource:", resource.name);
  }

  let containerRef: HTMLDivElement | null = $state(null);
  function handleBackgroundClick(event: MouseEvent): void {
    // Only clear if clicking directly on the container, not on children
    if (event.target === containerRef) {
      selectionState.clear();
    }
  }

  function handleBackgroundKeyDown(event: KeyboardEvent): void {
    // Handle keyboard interaction for the background area
    if (event.key === "Enter" || event.key === " ") {
      selectionState.clear();
    }
  }

  function handleKeyDown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      selectionState.clear();
    }
  }

  // Clear selection when path changes
  $effect(() => {
    path;
    selectionState.clear();
  });

  // Upload functionality
  function handleFileDrop(files: FileList): void {
    uploadState?.addFiles(files);
  }
</script>

<svelte:window onkeydown={handleKeyDown} />

<div class={cn("bg-background flex flex-col border", className)}>
  <!-- Main content area -->
  {#if mod_name}
    <FileZone onDrop={handleFileDrop} disabled={!uploadState} accept="image/*">
      <ScrollArea class="h-full w-full flex-1">
        {#if folders.length === 0 && resourceList.length === 0}
          <div class="text-muted-foreground flex h-full items-center justify-center">
            <p>This folder is empty</p>
          </div>
        {:else}
          <div
            class="flex h-full w-full flex-1 flex-wrap gap-1 p-4 outline-none"
            bind:this={containerRef}
            role="button"
            tabindex="0"
            onclick={handleBackgroundClick}
            onkeydown={handleBackgroundKeyDown}
            aria-label="Clear selection"
          >
            {#each folders as folderName}
              <ResourceFolder
                name={folderName}
                selected={selectionState.isFolderSelected(folderName)}
                handleSelect={(e) => handleFolderSelect(folderName, e)}
                handleOpen={() => handleFolderOpen(folderName)}
              />
            {/each}

            {#each resourceList as resource}
              <ResourceFile
                {mod_name}
                {resource}
                currentPath={path}
                selected={selectionState.isResourceSelected(resource.name)}
                handleSelect={(e) => handleResourceSelect(resource, e)}
                handleOpen={() => handleResourceOpen(resource)}
              />
            {/each}
          </div>
        {/if}
      </ScrollArea>
      <!-- Upload progress component -->
      {#if uploadState}
        <UploadProgress {uploadState} />
      {/if}
    </FileZone>
  {/if}

  <!-- Status bar -->
  <div class="bg-card border-border text-muted-foreground border-t px-4 py-2 text-xs">
    {folders.length} folder{folders.length !== 1 ? "s" : ""},
    {resourceList.length} resource{resourceList.length !== 1 ? "s" : ""}
    {#if selectionState.count > 0}
      <span class="ml-2">
        | {selectionState.count} selected
      </span>
    {/if}
  </div>
</div>
