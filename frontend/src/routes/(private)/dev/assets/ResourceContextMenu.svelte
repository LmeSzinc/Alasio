<script lang="ts">
  import {
      AlertDialog,
      AlertDialogAction,
      AlertDialogCancel,
      AlertDialogContent,
      AlertDialogDescription,
      AlertDialogFooter,
      AlertDialogHeader,
      AlertDialogTitle,
  } from "$lib/components/ui/alert-dialog";
  import * as ContextMenuPrimitive from "$lib/components/ui/context-menu";
  import { Input } from "$lib/components/ui/input";
  import { Label } from "$lib/components/ui/label";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { Separator } from "$lib/components/ui/separator";
  import type { TopicLifespan } from "$lib/ws";
  import { Edit, File, FileCheck, FileSymlink, FileX, Folder, FolderPlus, Trash2, Upload } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { resourceSelection } from "./selected.svelte";
  import type { FolderResponse } from "./types";

  let {
    topicClient,
    children,
    onUploadClick,
  }: {
    topicClient: TopicLifespan<FolderResponse>;
    children: Snippet;
    onUploadClick?: () => void;
  } = $props();

  const deleteRpc = topicClient.rpc();
  const renameRpc = topicClient.rpc();
  const trackRpc = topicClient.rpc();
  const untrackRpc = topicClient.rpc();
  const resourceToAssetRpc = topicClient.rpc();
  const createFolderRpc = topicClient.rpc();

  const hasSelection = $derived(resourceSelection.count > 0);
  const isSingleSelection = $derived(resourceSelection.count === 1);

  let showDeleteDialog = $state(false);
  let showRenameDialog = $state(false);
  let showCreateFolderDialog = $state(false);
  let newName = $state("");
  let folderName = $state("");
  let contextMenuOpen = $state(false);

  // Get selected items grouped by type
  const selectedResources = $derived(
    resourceSelection.selectedItems.filter((item) => item.type === "resource").map((item) => item.name),
  );
  const selectedFolders = $derived(
    resourceSelection.selectedItems.filter((item) => item.type === "folder").map((item) => item.name),
  );

  // Determine what types are selected
  const hasResources = $derived(selectedResources.length > 0);
  const hasFolders = $derived(selectedFolders.length > 0);
  const hasMixedSelection = $derived(hasResources && hasFolders);

  function handleUpload(): void {
    contextMenuOpen = false;
    if (onUploadClick) {
      onUploadClick();
    }
  }

  function openCreateFolderDialog(): void {
    contextMenuOpen = false;
    folderName = "";
    showCreateFolderDialog = true;
  }

  function handleCreateFolder(): void {
    if (folderName.trim()) {
      createFolderRpc.call("create_folder", { name: folderName.trim() });
      showCreateFolderDialog = false;
    }
  }

  function openRenameDialog(): void {
    if (isSingleSelection) {
      newName = resourceSelection.selectedItems[0].name;
      showRenameDialog = true;
    }
    contextMenuOpen = false;
  }

  function handleRename(): void {
    if (!isSingleSelection || !newName.trim()) return;

    const item = resourceSelection.selectedItems[0];
    if (item.type === "resource") {
      renameRpc.call("rename_resource", {
        old_name: item.name,
        new_name: newName.trim(),
      });
    } else {
      renameRpc.call("rename_folder", {
        old_name: item.name,
        new_name: newName.trim(),
      });
    }
    showRenameDialog = false;
  }

  function openDeleteDialog(): void {
    contextMenuOpen = false;
    showDeleteDialog = true;
  }

  function handleDelete(): void {
    if (selectedResources.length > 0) {
      deleteRpc.call("delete_resource", { names: selectedResources });
    }
    if (selectedFolders.length > 0) {
      deleteRpc.call("delete_folder", { names: selectedFolders });
    }
    resourceSelection.clear();
    showDeleteDialog = false;
  }

  function handleTrack(): void {
    if (selectedResources.length > 0) {
      trackRpc.call("resource_track", { names: selectedResources });
      resourceSelection.clear();
    }
    contextMenuOpen = false;
  }

  function handleUntrack(): void {
    if (selectedResources.length > 0) {
      untrackRpc.call("resource_untrack", { names: selectedResources });
      resourceSelection.clear();
    }
    contextMenuOpen = false;
  }

  function handleResourceToAsset(): void {
    if (selectedResources.length > 0) {
      resourceToAssetRpc.call("resource_to_asset", { names: selectedResources });
      resourceSelection.clear();
    }
    contextMenuOpen = false;
  }
</script>

<ContextMenuPrimitive.Root bind:open={contextMenuOpen}>
  <ContextMenuPrimitive.Trigger class="h-full w-full">
    {@render children()}
  </ContextMenuPrimitive.Trigger>

  <ContextMenuPrimitive.Content class="w-56">
    <!-- Upload and Create Folder -->
    <ContextMenuPrimitive.Item onclick={handleUpload}>
      <Upload class="mr-2 h-4 w-4" />
      <span>Upload File</span>
    </ContextMenuPrimitive.Item>

    <ContextMenuPrimitive.Item onclick={openCreateFolderDialog}>
      <FolderPlus class="mr-2 h-4 w-4" />
      <span>New Folder</span>
    </ContextMenuPrimitive.Item>

    <Separator class="my-1" />

    <!-- Rename - only enabled for single selection -->
    <ContextMenuPrimitive.Item disabled={!isSingleSelection} onclick={openRenameDialog}>
      <Edit class="mr-2 h-4 w-4" />
      <span>Rename</span>
    </ContextMenuPrimitive.Item>

    <!-- Track/Untrack - only for resources, disabled for folders or mixed selection -->
    <ContextMenuPrimitive.Item disabled={!hasResources || hasFolders} onclick={handleTrack}>
      <FileCheck class="mr-2 h-4 w-4" />
      <span>Track Resource</span>
    </ContextMenuPrimitive.Item>

    <ContextMenuPrimitive.Item disabled={!hasResources || hasFolders} onclick={handleUntrack}>
      <FileX class="mr-2 h-4 w-4" />
      <span>Untrack Resource</span>
    </ContextMenuPrimitive.Item>

    <!-- Resource to Asset - only for resources -->
    <ContextMenuPrimitive.Item disabled={!hasResources || hasFolders} onclick={handleResourceToAsset}>
      <FileSymlink class="mr-2 h-4 w-4" />
      <span>Resource to Asset</span>
    </ContextMenuPrimitive.Item>

    <Separator class="my-1" />

    <!-- Delete -->
    <ContextMenuPrimitive.Item
      disabled={!hasSelection}
      onclick={openDeleteDialog}
      class="text-destructive focus:text-destructive"
    >
      <Trash2 class="mr-2 h-4 w-4" />
      <span>Delete</span>
    </ContextMenuPrimitive.Item>
  </ContextMenuPrimitive.Content>
</ContextMenuPrimitive.Root>

<!-- Delete Confirmation Dialog -->
<AlertDialog bind:open={showDeleteDialog}>
  <AlertDialogContent class="max-w-2xl">
    <AlertDialogHeader>
      <AlertDialogTitle>Delete Confirmation</AlertDialogTitle>
      <AlertDialogDescription>
        Are you sure you want to delete the following items? This action cannot be undone.
      </AlertDialogDescription>
    </AlertDialogHeader>
    <ScrollArea class="max-h-[400px]">
      <div class="space-y-3 py-4">
        {#if selectedFolders.length > 0}
          <div>
            <div class="mb-2 text-sm font-medium">Folders ({selectedFolders.length}):</div>
            <div class="space-y-1">
              {#each selectedFolders as folder}
                <div class="flex items-center gap-2 pl-2 text-sm">
                  <Folder class="text-muted-foreground h-4 w-4" />
                  <span class="font-mono">{folder}</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}
        {#if selectedResources.length > 0}
          <div>
            <div class="mb-2 text-sm font-medium">Resources ({selectedResources.length}):</div>
            <div class="space-y-1">
              {#each selectedResources as resource}
                <div class="flex items-center gap-2 pl-2 text-sm">
                  <File class="text-muted-foreground h-4 w-4" />
                  <span class="font-mono">{resource}</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}
      </div>
    </ScrollArea>
    <AlertDialogFooter>
      <AlertDialogCancel>Cancel</AlertDialogCancel>
      <AlertDialogAction
        onclick={handleDelete}
        class="bg-destructive text-destructive-foreground hover:bg-destructive/90"
      >
        Delete {resourceSelection.count}
        {resourceSelection.count === 1 ? "item" : "items"}
      </AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>

<!-- Rename Dialog -->
<AlertDialog bind:open={showRenameDialog}>
  <AlertDialogContent>
    <AlertDialogHeader>
      <AlertDialogTitle>Rename</AlertDialogTitle>
      <AlertDialogDescription>
        Enter a new name for {isSingleSelection ? resourceSelection.selectedItems[0]?.name : ""}
      </AlertDialogDescription>
    </AlertDialogHeader>
    <div class="grid gap-4 py-4">
      <div class="grid gap-2">
        <Label for="rename-input">Name</Label>
        <Input
          id="rename-input"
          bind:value={newName}
          placeholder="Enter new name"
          onkeydown={(e) => {
            if (e.key === "Enter") handleRename();
          }}
        />
      </div>
    </div>
    <AlertDialogFooter>
      <AlertDialogCancel>Cancel</AlertDialogCancel>
      <AlertDialogAction onclick={handleRename} disabled={!newName.trim()}>Rename</AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>

<!-- Create Folder Dialog -->
<AlertDialog bind:open={showCreateFolderDialog}>
  <AlertDialogContent>
    <AlertDialogHeader>
      <AlertDialogTitle>Create New Folder</AlertDialogTitle>
      <AlertDialogDescription>Enter a name for the new folder</AlertDialogDescription>
    </AlertDialogHeader>
    <div class="grid gap-4 py-4">
      <div class="grid gap-2">
        <Label for="folder-name-input">Folder Name</Label>
        <Input
          id="folder-name-input"
          bind:value={folderName}
          placeholder="Enter folder name"
          onkeydown={(e) => {
            if (e.key === "Enter") handleCreateFolder();
          }}
        />
      </div>
    </div>
    <AlertDialogFooter>
      <AlertDialogCancel>Cancel</AlertDialogCancel>
      <AlertDialogAction onclick={handleCreateFolder} disabled={!folderName.trim()}>Create</AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>
