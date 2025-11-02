import { SelectionState } from "./selectionState.svelte";

// --- Type definitions for selection items ---

export type ResourceSelectionItem = { type: "folder"; name: string } | { type: "resource"; name: string };

export type AssetSelectionItem = { type: "asset"; name: string };

export type TemplateSelectionItem = { type: "template"; file: string };

// --- Key extractor functions ---

/**
 * Generates a unique key for items in the ResourceManager.
 * e.g., "folder:combat" or "resource:~BATTLE_PREPARATION.png"
 */
function getResourceItemKey(item: ResourceSelectionItem): string {
  return `${item.type}:${item.name}`;
}

/**
 * Generates a unique key for items in the AssetList.
 * e.g., "asset:BATTLE_PREPARATION"
 */
function getAssetItemKey(item: AssetSelectionItem): string {
  return `asset:${item.name}`;
}

/**
 * Generates a unique key for items in the TemplateList.
 * e.g., "template:asset/combat/BATTLE_PREPARATION/cn.png"
 */
function getTemplateItemKey(item: TemplateSelectionItem): string {
  return `template:${item.file}`;
}

// --- Export singleton instances ---

/**
 * Selection state for ResourceManager (folders and resource files).
 */
export const resourceSelection = new SelectionState<ResourceSelectionItem>(getResourceItemKey);

/**
 * Selection state for AssetList (assets).
 */
export const assetSelection = new SelectionState<AssetSelectionItem>(getAssetItemKey);

/**
 * Selection state for TemplateList (templates).
 */
export const templateSelection = new SelectionState<TemplateSelectionItem>(getTemplateItemKey);
