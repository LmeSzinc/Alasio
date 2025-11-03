<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import AssetContextMenu from "./AssetContextMenu.svelte";
  import AssetDisplay from "./AssetDisplay.svelte";
  import { assetSelection, type AssetSelectionItem } from "./selected.svelte";
  import TemplateImage from "./TemplateImage.svelte";
  import type { FolderResponse, MetaAsset } from "./types";
  import type { TopicLifespan } from "$lib/ws";

  let {
    assetList,
    mod_name,
    topicClient,
  }: {
    assetList: MetaAsset[];
    mod_name: string;
    topicClient: TopicLifespan<FolderResponse>;
  } = $props();

  const renameAssetRpc = topicClient.rpc();

  // Create a flat list of all assets for range selection
  const allItems = $derived<AssetSelectionItem[]>(assetList.map((a) => ({ type: "asset", name: a.name })));

  /**
   * Handles selection logic (Click, Ctrl+Click, Shift+Click) using the global assetSelection state.
   */
  function handleAssetSelect(assetName: string, event: MouseEvent): void {
    const item: AssetSelectionItem = { type: "asset", name: assetName };

    if (event.shiftKey) {
      assetSelection.selectRange(allItems, item);
    } else if (event.ctrlKey || event.metaKey) {
      assetSelection.toggle(item);
    } else {
      assetSelection.select(item);
    }
  }

  /**
   * Handle renaming an asset
   */
  function handleAssetRename(oldName: string, newName: string): void {
    renameAssetRpc.call("asset_rename", { old_name: oldName, new_name: newName });
  }

  /**
   * Handles right-click on items.
   * If the right-clicked item is not in the current selection, select only that item.
   * This mimics standard file explorer behavior.
   */
  function handleContextMenu(item: AssetSelectionItem, event: MouseEvent): void {
    // Check if the right-clicked item is already selected
    if (!assetSelection.isSelected(item)) {
      // If not selected, select only this item (replace current selection)
      assetSelection.select(item);
    }
    // If already selected, keep the current selection (allow multi-item context menu)
  }

  /**
   * Handle keyboard events on individual assets
   */
  function handleItemKeyDown(item: AssetSelectionItem, event: KeyboardEvent): void {
    // Don't handle if we're currently renaming
    if (assetSelection.renamingItem) {
      return;
    }
    // F2: Start renaming if this item is selected
    if (event.key === "F2") {
      event.preventDefault();
      event.stopPropagation();
      if (assetSelection.isSelected(item)) {
        assetSelection.startRenaming(item);
      }
      return;
    }
  }

  /**
   * Handle keyboard events on the container level
   */
  function handleContainerKeyDown(event: KeyboardEvent): void {
    // Only handle if the container or its children have focus
    // and we're not currently renaming
    if (assetSelection.renamingItem) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      if (assetSelection.renamingItem) {
        assetSelection.stopRenaming();
        return;
      }
      assetSelection.clear();
    }
  }
</script>

<AssetContextMenu {topicClient}>
  <ScrollArea class="h-full">
    <div
      class="flex flex-col gap-1 p-1 outline-none"
      role="grid"
      tabindex="0"
      onkeydown={(e) => {
        handleContainerKeyDown(e);
      }}
      aria-label="Asset list"
    >
      {#if assetList.length === 0}
        <div class="text-muted-foreground flex h-full items-center justify-center p-4 text-sm">
          No assets in this folder.
        </div>
      {:else}
        {#each assetList as asset (asset.name)}
          {@const item: AssetSelectionItem = { type: "asset", name: asset.name }}
          <AssetDisplay
            name={asset.name}
            {item}
            handleSelect={(e) => handleAssetSelect(asset.name, e)}
            handleRename={handleAssetRename}
            oncontextmenu={(e) => handleContextMenu(item, e)}
            onkeydown={(e) => handleItemKeyDown(item, e)}
          >
            {#snippet content()}
              {#if asset.templates?.[0]}
                <TemplateImage template={asset.templates[0]} {mod_name} class="h-9 w-16" />
              {/if}
            {/snippet}
          </AssetDisplay>
        {/each}
      {/if}
    </div>
  </ScrollArea>
</AssetContextMenu>
