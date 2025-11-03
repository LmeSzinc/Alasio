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
  import { Edit, FilePlus, Package, Trash2 } from "@lucide/svelte";
  import type { Snippet } from "svelte";
  import { assetSelection } from "./selected.svelte";
  import type { FolderResponse } from "./types";

  let {
    topicClient,
    children,
  }: {
    topicClient: TopicLifespan<FolderResponse>;
    children: Snippet;
  } = $props();

  const addAssetRpc = topicClient.rpc();
  const deleteAssetRpc = topicClient.rpc();
  const renameAssetRpc = topicClient.rpc();

  const hasSelection = $derived(assetSelection.count > 0);
  const isSingleSelection = $derived(assetSelection.count === 1);

  let showDeleteDialog = $state(false);
  let showAddAssetDialog = $state(false);
  let assetName = $state("");
  let contextMenuOpen = $state(false);

  // Get selected asset names
  const selectedAssets = $derived(assetSelection.selectedItems.map((item) => item.name));

  /**
   * Open dialog to add a new asset
   */
  function openAddAssetDialog(): void {
    contextMenuOpen = false;
    assetName = "";
    showAddAssetDialog = true;
  }

  /**
   * Handle adding a new asset
   */
  function handleAddAsset(): void {
    if (assetName.trim()) {
      addAssetRpc.call("asset_add", { name: assetName.trim() });
      showAddAssetDialog = false;
      assetName = "";
    }
  }

  /**
   * Start inline renaming for a single selected asset
   */
  function startRename(): void {
    if (isSingleSelection) {
      const item = assetSelection.selectedItems[0];
      assetSelection.startRenaming(item);
    }
    contextMenuOpen = false;
  }

  /**
   * Open delete confirmation dialog
   */
  function openDeleteDialog(): void {
    contextMenuOpen = false;
    showDeleteDialog = true;
  }

  /**
   * Handle deleting selected assets
   */
  function handleDelete(): void {
    if (selectedAssets.length > 0) {
      deleteAssetRpc.call("asset_del", { names: selectedAssets });
    }
    assetSelection.clear();
    showDeleteDialog = false;
  }
</script>

<ContextMenuPrimitive.Root bind:open={contextMenuOpen}>
  <ContextMenuPrimitive.Trigger class="h-full w-full">
    {@render children()}
  </ContextMenuPrimitive.Trigger>

  <ContextMenuPrimitive.Content class="w-56">
    <!-- Add Asset -->
    <ContextMenuPrimitive.Item onclick={openAddAssetDialog}>
      <FilePlus class="mr-2 h-4 w-4" />
      <span>New Asset</span>
    </ContextMenuPrimitive.Item>

    <Separator class="my-1" />

    <!-- Rename - only enabled for single selection -->
    <ContextMenuPrimitive.Item disabled={!isSingleSelection} onclick={startRename}>
      <Edit class="mr-2 h-4 w-4" />
      <span>Rename</span>
    </ContextMenuPrimitive.Item>

    <Separator class="my-1" />

    <!-- Delete -->
    <ContextMenuPrimitive.Item disabled={!hasSelection} onclick={openDeleteDialog}>
      <Trash2 class="text-destructive mr-2 h-4 w-4" />
      <span>Delete</span>
    </ContextMenuPrimitive.Item>
  </ContextMenuPrimitive.Content>
</ContextMenuPrimitive.Root>

<!-- Add Asset Dialog -->
<AlertDialog bind:open={showAddAssetDialog}>
  <AlertDialogContent>
    <AlertDialogHeader>
      <AlertDialogTitle>Create New Asset</AlertDialogTitle>
      <AlertDialogDescription>
        Enter a name for the new asset. The asset name should follow uppercase naming conventions (e.g.
        BATTLE_PREPARATION).
      </AlertDialogDescription>
    </AlertDialogHeader>
    <div class="grid gap-4 py-4">
      <div class="grid gap-2">
        <Label for="asset-name-input">Asset Name</Label>
        <Input
          id="asset-name-input"
          bind:value={assetName}
          placeholder="e.g., BATTLE_PREPARATION"
          onkeydown={(e) => {
            if (e.key === "Enter") handleAddAsset();
          }}
        />
      </div>
    </div>
    <AlertDialogFooter>
      <AlertDialogCancel>Cancel</AlertDialogCancel>
      <AlertDialogAction onclick={handleAddAsset} disabled={!assetName.trim()}>Create</AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>

<!-- Delete Confirmation Dialog -->
<AlertDialog bind:open={showDeleteDialog}>
  <AlertDialogContent class="max-w-2xl">
    <AlertDialogHeader>
      <AlertDialogTitle>Delete Confirmation</AlertDialogTitle>
      <AlertDialogDescription>
        Are you sure you want to delete the following assets? This action cannot be undone.
      </AlertDialogDescription>
    </AlertDialogHeader>
    <ScrollArea class="max-h-[400px]">
      <div class="space-y-3 py-4">
        {#if selectedAssets.length > 0}
          <div>
            <div class="mb-2 text-sm font-medium">Assets ({selectedAssets.length}):</div>
            <div class="space-y-1">
              {#each selectedAssets as asset}
                <div class="flex items-center gap-2 pl-2 text-sm">
                  <Package class="text-muted-foreground h-4 w-4" />
                  <span class="font-mono">{asset}</span>
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
        Delete {assetSelection.count}
        {assetSelection.count === 1 ? "asset" : "assets"}
      </AlertDialogAction>
    </AlertDialogFooter>
  </AlertDialogContent>
</AlertDialog>
