<script lang="ts">
  import { cn } from "$lib/utils";
  import type { TopicLifespan } from "$lib/ws";
  import AssetList from "./AssetList.svelte";
  import { assetSelection, templateSelection } from "./selected.svelte";
  import TemplateList from "./TemplateList.svelte";
  import type { FolderResponse, MetaAsset } from "./types";

  let {
    topicClient,
    mod_name,
    path,
    class: className,
  }: {
    topicClient: TopicLifespan<FolderResponse>;
    mod_name: string;
    path: string;
    class?: string;
  } = $props();

  const assets = $derived(topicClient.data?.assets || {});
  const assetList = $derived<MetaAsset[]>(Object.values(assets));

  /**
   * The single asset to be displayed in TemplateList.
   * This is the core logic: it's only populated if exactly ONE asset is selected.
   */
  const viewedAsset = $derived.by<MetaAsset | null>(() => {
    if (assetSelection.selectedItems.length === 1) {
      const selectedAssetName = assetSelection.selectedItems[0].name;
      return assets[selectedAssetName] ?? null;
    }
    return null; // Return null if 0 or >1 assets are selected
  });

  /**
   * Effect to auto-select the first asset when none is selected.
   * This ensures there's always a default selection when assets are available.
   */
  $effect(() => {
    if (assetList.length > 0 && assetSelection.selectedItems.length === 0) {
      assetSelection.select({ type: "asset", name: assetList[0].name });
    }
  });

  /**
   * Effect to clear all asset-related selections when the folder path changes.
   */
  $effect(() => {
    path; // This effect depends on the path
    assetSelection.clear();
    templateSelection.clear();
  });

  /**
   * Effect to clear template selections whenever the selected asset(s) change.
   * This prevents stale template selections when the user clicks a different asset.
   */
  $effect(() => {
    assetSelection.selectedItems; // This effect depends on the asset selection
    templateSelection.clear();
  });
</script>

<div class={cn("flex h-full w-full flex-row border-t", className)}>
  <!-- Left Column: Asset List -->
  <div class="w-1/3 border-r">
    <AssetList {assetList} {mod_name} />
  </div>

  <!-- Right Column: Template List -->
  <div class="w-2/3 flex-1">
    <TemplateList asset={viewedAsset} {mod_name} />
  </div>
</div>
