<script lang="ts">
  import { Input } from "$lib/components/ui/input";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { FileZone, UploadProgress, UploadState } from "$lib/components/upload";
  import { cn } from "$lib/utils";
  import type { TopicLifespan } from "$lib/ws";
  import ResourceFile from "./ResourceFile.svelte";
  import ResourceFolder from "./ResourceFolder.svelte";
  import ResourceContextMenu from "./ResourceContextMenu.svelte";
  import { resourceSelection, type ResourceSelectionItem } from "./selected.svelte";
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
  const renameRpc = topicClient.rpc();

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

  // Create a flat list of all items (folders + resources) for range selection.
  const allItems = $derived<ResourceSelectionItem[]>([
    ...folders.map((name) => ({ type: "folder" as const, name })),
    ...resourceList.map((r) => ({ type: "resource" as const, name: r.displayName })),
  ]);

  /**
   * Handles folder selection logic using the global resourceSelection state.
   */
  function handleFolderSelect(folderName: string, event: MouseEvent): void {
    const item: ResourceSelectionItem = { type: "folder" as const, name: folderName };

    if (event.shiftKey) {
      resourceSelection.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      resourceSelection.toggle(item);
    } else {
      resourceSelection.select(item);
    }
  }

  function handleFolderOpen(folderName: string): void {
    const newPath = path ? `${path}/${folderName}` : folderName;
    onNavigate?.(newPath);
  }

  /**
   * Handle folder rename
   */
  function handleFolderRename(oldName: string, newName: string): void {
    renameRpc.call("rename_folder", {
      old_name: oldName,
      new_name: newName,
    });
  }

  /**
   * Handles resource file selection logic using the global resourceSelection state.
   */
  function handleResourceSelect(resource: ResourceItem, event: MouseEvent): void {
    const item: ResourceSelectionItem = { type: "resource" as const, name: resource.displayName };

    if (event.shiftKey) {
      resourceSelection.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      resourceSelection.toggle(item);
    } else {
      resourceSelection.select(item);
    }
  }

  function handleResourceOpen(resource: ResourceItem): void {
    // TODO: Implement resource open logic
    console.log("Open resource:", resource.displayName);
  }

  /**
   * Handle resource rename
   */
  function handleResourceRename(oldName: string, newName: string): void {
    renameRpc.call("rename_resource", {
      old_name: oldName,
      new_name: newName,
    });
  }

  let containerRef: HTMLDivElement | null = $state(null);
  function handleBackgroundClick(event: MouseEvent): void {
    if (event.target === containerRef) {
      resourceSelection.clear();
    }
  }

  function handleBackgroundContextMenu(event: MouseEvent): void {
    if (event.target === containerRef) {
      resourceSelection.clear();
    }
  }

  function handleBackgroundKeyDown(event: KeyboardEvent): void {
    // Handle keyboard interaction for the background area
    if (event.key === "Enter" || event.key === " ") {
      resourceSelection.clear();
    }
  }

  /**
   * Handle keyboard events on the container level (not window level)
   * This prevents conflicts with other components on the same page
   */
  function handleContainerKeyDown(event: KeyboardEvent): void {
    // Only handle if the container or its children have focus
    // and we're not currently renaming
    if (resourceSelection.renamingItem) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      if (resourceSelection.renamingItem) {
        resourceSelection.stopRenaming();
        return;
      }
      resourceSelection.clear();
    }
  }

  /**
   * Handle keyboard events for individual items (folders/resources)
   * This is called from the gridcell div that wraps each item
   */
  function handleItemKeyDown(item: ResourceSelectionItem, event: KeyboardEvent): void {
    // Don't handle if we're currently renaming
    if (resourceSelection.renamingItem) {
      return;
    }
    // F2: Start renaming if this item is selected
    if (event.key === "F2") {
      event.preventDefault();
      event.stopPropagation();
      if (resourceSelection.isSelected(item)) {
        resourceSelection.startRenaming(item);
      }
      return;
    }
    // Enter: Open the item
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      if (item.type === "folder") {
        handleFolderOpen(item.name);
      } else {
        const resource = resourceList.find((r) => r.displayName === item.name);
        if (resource) {
          handleResourceOpen(resource);
        }
      }
      return;
    }
  }

  /**
   * Handle click on gridcell to ensure it gets focus
   * This is critical for keyboard events to work properly
   */
  function handleItemClick(item: ResourceSelectionItem, event: MouseEvent): void {
    // Make sure the gridcell gets focus when clicked
    // Without this, focus might go to inner elements (like images) and keyboard events won't work
    const gridcell = event.currentTarget as HTMLElement;
    if (gridcell && document.activeElement !== gridcell) {
      gridcell.focus();
    }
  }

  /**
   * Handles context menu (right-click) on items.
   * If the right-clicked item is not in the current selection, select only that item.
   * This mimics standard file explorer behavior.
   */
  function handleContextMenu(item: ResourceSelectionItem, event: MouseEvent): void {
    // Check if the right-clicked item is already selected
    if (!resourceSelection.isSelected(item)) {
      // If not selected, select only this item (replace current selection)
      resourceSelection.select(item);
    }
    // If already selected, keep the current selection (allow multi-item context menu)
  }

  // Effect to clear selection when the folder path changes.
  $effect(() => {
    path;
    resourceSelection.clear();
  });

  let fileInput: HTMLInputElement | null = $state(null);

  // Upload functionality
  function handleFileDrop(files: FileList): void {
    uploadState?.addFiles(files);
  }

  function handleFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      uploadState?.addFiles(input.files);
    }
  }

  function onUploadClick(): void {
    fileInput?.click();
  }
</script>

<Input type="file" multiple accept="image/*" class="hidden" bind:ref={fileInput} onchange={handleFileChange} />

<div class={cn("bg-background flex flex-col border", className)}>
  <!-- Main content area -->
  {#if mod_name}
    <FileZone onDrop={handleFileDrop} disabled={!uploadState} accept="image/*">
      <!-- Wrap content with ResourceContextMenu -->
      <ResourceContextMenu {topicClient} {onUploadClick}>
        {#snippet children()}
          <ScrollArea class="h-full w-full flex-1">
            {#if folders.length === 0 && resourceList.length === 0}
              <div class="text-muted-foreground flex h-full items-center justify-center">
                <p>This folder is empty</p>
              </div>
            {:else}
              <div
                class="flex h-full w-full flex-1 flex-wrap gap-1 px-4 py-2 outline-none"
                bind:this={containerRef}
                role="grid"
                tabindex="0"
                onclick={handleBackgroundClick}
                oncontextmenu={handleBackgroundContextMenu}
                onkeydown={(e) => {
                  handleBackgroundKeyDown(e);
                  handleContainerKeyDown(e);
                }}
                aria-label="Resource list"
              >
                {#each folders as folderName}
                  {@const item = { type: "folder" as const, name: folderName }}
                  <ResourceFolder
                    name={folderName}
                    {item}
                    handleSelect={(e) => handleFolderSelect(folderName, e)}
                    handleOpen={() => handleFolderOpen(folderName)}
                    handleRename={handleFolderRename}
                    oncontextmenu={(e) => handleContextMenu(item, e)}
                    onkeydown={(e) => handleItemKeyDown(item, e)}
                    onclick={(e) => handleItemClick(item, e)}
                  />
                {/each}

                {#each resourceList as resource}
                  {@const item = { type: "resource" as const, name: resource.displayName }}
                  <ResourceFile
                    {mod_name}
                    resourceItem={resource}
                    {item}
                    currentPath={path}
                    handleSelect={(e) => handleResourceSelect(resource, e)}
                    handleOpen={() => handleResourceOpen(resource)}
                    handleRename={handleResourceRename}
                    oncontextmenu={(e) => handleContextMenu(item, e)}
                    onkeydown={(e) => handleItemKeyDown(item, e)}
                    onclick={(e) => handleItemClick(item, e)}
                  />
                {/each}
              </div>
            {/if}
          </ScrollArea>
        {/snippet}
      </ResourceContextMenu>
      <!-- Upload progress component -->
      {#if uploadState}
        <UploadProgress {uploadState} />
      {/if}
    </FileZone>
  {/if}
</div>
