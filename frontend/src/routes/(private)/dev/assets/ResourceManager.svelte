<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import PathBreadcrumb from "./PathBreadcrumb.svelte";
  import ResourceFile from "./ResourceFile.svelte";
  import ResourceFolder from "./ResourceFolder.svelte";
  import { selectionState } from "./selectionState.svelte";
  import type { FolderResponse, ResourceItem } from "./types";

  let {
    mod_name,
    path,
    folderData,
    onNavigate,
  }: {
    mod_name: string;
    path: string;
    folderData?: FolderResponse;
    onNavigate?: (newPath: string) => void;
  } = $props();

  const folders = $derived(folderData?.folders || []);
  const resources = $derived(folderData?.resources || {});
  const mod_path_assets = $derived(folderData?.mod_path_assets || "assets");

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
    console.log(event.target, containerRef);
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
      selectionState.clear();
    }
  }

  // Clear selection when path changes
  $effect(() => {
    path;
    selectionState.clear();
  });
</script>

<svelte:window onkeydown={handleKeyDown} />

<div class="bg-background flex h-1/2 w-1/2 flex-col border">
  <!-- Navigation bar with breadcrumb -->
  <div class="bg-card border-border flex items-center gap-3 border-b px-4 py-3">
    <PathBreadcrumb {mod_path_assets} {path} {onNavigate} />
  </div>

  <!-- Main content area -->
  {#if mod_name}
    <ScrollArea class="h-full w-full flex-1">
      {#if folders.length === 0 && resourceList.length === 0}
        <div class="text-muted-foreground flex h-full items-center justify-center">
          <p>This folder is empty</p>
        </div>
      {:else}
        <div
          class="flex h-full w-full flex-1 flex-wrap gap-1 p-4"
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
