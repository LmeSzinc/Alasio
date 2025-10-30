<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";
  import { assetSelection, type AssetSelectionItem } from "./selected.svelte";
  import TemplateImage from "./TemplateImage.svelte";
  import type { MetaAsset } from "./types";

  let {
    assetList,
    mod_name,
  }: {
    assetList: MetaAsset[];
    mod_name: string;
  } = $props();

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
</script>

<ScrollArea class="h-full">
  <div class="flex flex-col gap-1 p-1">
    {#if assetList.length === 0}
      <div class="text-muted-foreground flex h-full items-center justify-center p-4 text-sm">
        No assets in this folder.
      </div>
    {:else}
      {#each assetList as asset (asset.name)}
        {@const selected = assetSelection.isSelected({ type: "asset", name: asset.name })}
        <button
          onclick={(e) => handleAssetSelect(asset.name, e)}
          class={cn(
            "group flex w-full cursor-pointer items-center justify-between rounded-md p-1.5",
            "cursor-pointer overflow-hidden rounded-md outline-none shadow-sm",
            "text-card-foreground font-consolas text-xs",
            selected ? "bg-primary text-white" : "bg-card",
          )}
        >
          <span
            class={cn(
              "line-clamp-2 flex-1 px-2 text-left break-all",
              selected ? "text-white" : "group-hover:text-primary",
            )}
            >{asset.name}
          </span>
          {#if asset.templates?.[0]}
            <TemplateImage template={asset.templates[0]} {mod_name} class="h-9 w-16" />
          {/if}
        </button>
      {/each}
    {/if}
  </div>
</ScrollArea>
