<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { FileZone } from "$lib/components/upload";
  import { cn } from "$lib/utils";
  import type { Rpc, TopicLifespan } from "$lib/ws";
  import ResourceContextMenu from "./ResourceContextMenu.svelte";
  import ResourceFile from "./ResourceFile.svelte";
  import ResourceFolder from "./ResourceFolder.svelte";
  import { resourceSelection, type ResourceSelectionItem } from "./selected.svelte";
  import type { FolderResponse, ResourceItem } from "./types";

  let {
    mod_name,
    path,
    topicClient,
    class: className,
  }: {
    mod_name: string;
    path: string;
    topicClient: TopicLifespan<FolderResponse>;
    class?: string;
  } = $props();

  const pathRpc = topicClient.rpc();
  const renameRpc = topicClient.rpc();

  // FileZone reference for opening file picker
  let fileZone: FileZone | null = $state(null);

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
    ...resourceList.map((r) => ({ type: "resource" as const, name: r.name })),
  ]);

  /**
   * Handle file upload
   * This is called by FileZone's UploadState for each file
   */
  function handleUpload(file: File): Rpc {
    // Create a dedicated RPC instance for this upload
    const rpc = topicClient.rpc();

    // Read file and convert to base64
    const reader = new FileReader();

    reader.onload = () => {
      const base64Data = reader.result as string;
      const base64Content = base64Data.split(",")[1] || base64Data;

      // Call RPC with file data
      rpc.call("resource_add_base64", {
        source: file.name,
        data: base64Content,
      });
    };

    reader.onerror = () => {
      // If file reading fails, we still return the RPC but it will error
      console.error(`Failed to read file: ${file.name}`);
    };

    reader.readAsDataURL(file);

    // Return the RPC instance immediately
    // UploadState will track its state reactively
    return rpc;
  }

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
    const item: ResourceSelectionItem = { type: "resource" as const, name: resource.name };
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
    renameRpc.call(
      "resource_rename",
      {
        old_name: oldName,
        new_name: newName,
      },
      {
        onSuccess: () => {
          // Select new name on RPC success
          resourceSelection.select({ type: "resource" as const, name: `~${newName}` });
        },
      },
    );
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

  /**
   * Open file picker when upload is clicked
   */
  function onUploadClick(): void {
    fileZone?.openFilePicker();
  }

  // Effect to clear selection when the folder path changes.
  $effect(() => {
    path;
    resourceSelection.clear();
  });
</script>

<div class={cn("bg-background flex flex-col border", className)}>
  <!-- Main content area -->
  {#if mod_name}
    <FileZone bind:this={fileZone} onUpload={handleUpload} accept="image/*">
      <!-- Wrap content with ResourceContextMenu -->
      <ResourceContextMenu {topicClient} {onUploadClick}>
        <ScrollArea class="h-full w-full flex-1">
          {#if folders.length === 0 && resourceList.length === 0}
            <div class="text-muted-foreground flex h-full items-center justify-center">
              <p>This folder is empty</p>
            </div>
          {:else}
            <div
              class="flex h-full w-full flex-1 flex-wrap gap-1 p-1 outline-none"
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
                />
              {/each}

              {#each resourceList as resource}
                {@const item = { type: "resource" as const, name: resource.name }}
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
                />
              {/each}
            </div>
          {/if}
        </ScrollArea>
      </ResourceContextMenu>
    </FileZone>
  {/if}
</div>
