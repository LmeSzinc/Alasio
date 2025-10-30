// Type definitions mirroring backend Python models.
// These interfaces represent the data structures returned by the backend API.

// Represents a rectangular area: [x1, y1, x2, y2].
// Corresponds to Area class in Python backend.
export type Area = [number, number, number, number];

// Represents an RGB color: [r, g, b].
// Corresponds to RGB class in Python backend.
export type RGB = [number, number, number];

// Template with metadata.
// Corresponds to MetaTemplate struct in Python backend.
export interface MetaTemplate {
  // Area to crop from resource image
  // (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
  area: Area;
  // Average color of cropped image, (r, g, b)
  color: RGB;

  // Mark assets as server specific, empty string '' for shared assets
  // Example, assets with lang=='cn' and server=='' will be loaded on CN, assets from other lang won't.
  lang: string;
  // Frame index.
  // If any frame matched, considered as template matched.
  // This is useful to do appear(...) on multiple images
  // to handle dynamic contents or contents with minor difference
  frame: number;

  // Area to search from screenshot, `search` is 20px outer pad of `area` by default
  // values in `search` can be negative meaning right aligned, this is useful for dynamic resolution ratio
  search?: Area;
  // Area to click if template is match exactly on `area`
  // If matched result moves, `button` moves accordingly.
  // This is useful when you do appear_then_click(...) on an movable content, click area is auto moved.
  button?: Area;
  // A match function that receives image and returns MatchResult
  // function will be patched onto match(), default to Template.match_template_luma_color
  match?: string;

  // Template matching similarity, 0 to 1, bigger for more similar
  // result similarity > 0.85 is considered matched
  similarity?: number;
  // Average color matching threshold, 0 to 255, smaller for more similar
  // Average color difference < 30 considered matched
  colordiff?: number;
  // from mod root to template file, will be set at runtime
  file: string;

  // --- meta attributes ---
  source?: string;
  // whether template file exists, default to True and will be set in AssetFolder
  file_exist: boolean;
  // whether resource file exists, default to True and will be set in AssetFolder
  source_exist: boolean;
}

const META_TEMPLATE_DEFAULTS = {
  lang: "",
  frame: 1,
  file: "",
  file_exist: true,
  source_exist: true,
};
export function createMetaTemplateWithDefaults(apiTemplate: Partial<MetaTemplate>): MetaTemplate {
  return {
    ...META_TEMPLATE_DEFAULTS,
    ...apiTemplate,
  } as MetaTemplate;
}

// Asset with metadata.
// Corresponds to MetaAsset struct in Python backend.
export interface MetaAsset {
  // asset folder from mod root, e.g. "asset/combat"
  path: string;
  // asset name, e.g. "BATTLE_PREPARATION"
  name: string;

  search?: Area;
  button?: Area;
  match?: string;
  similarity?: number;
  colordiff?: number;
  // Ban current button after clicking
  // Example: interval=3 means directly return no match within 3 seconds and 6 screenshots
  // This is useful to avoid double-clicking as game client can't respond that fast
  interval?: number;

  // --- meta attributes ---
  // Asset-level docstring
  doc: string;
  // additional resource images for reference
  ref: string[];
  templates: MetaTemplate[];
}

const META_ASSET_DEFAULTS = {
  doc: "",
  ref: [],
  templates: [],
};
export function createMetaAssetWithDefaults(apiAsset: Partial<MetaAsset>): MetaAsset {
  const hydratedAsset = {
    ...META_ASSET_DEFAULTS,
    ...apiAsset,
  };
  hydratedAsset.templates = (apiAsset.templates || []).map(createMetaTemplateWithDefaults);
  return hydratedAsset as MetaAsset;
}

// Represents metadata about a single resource file.
// Corresponds to ResourceRow struct in Python backend.
export interface ResourceRow {
  // filename, resource filename always startswith "~"
  // e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
  name: string;
  // Status of the resource file:
  // - 'tracked': tracked in resource.json and local file exists
  // - 'not_tracked': local file exists but not tracked in resource.json
  // - 'not_downloaded': tracked in resource.json but local file doesn't exist
  status: "tracked" | "not_tracked" | "not_downloaded";
}

// Represents a complete folder's contents from the backend.
// Corresponds to FolderResponse struct in Python backend.
export interface FolderResponse {
  mod_name: string;
  // Relative path from mod root to assets folder. e.g. assets
  // `path` must startswith `mod_path_assets`
  mod_path_assets: string;
  // Relative path from project root to assets folder. e.g. assets/combat
  path: string;
  // Sub-folders, list of folder names
  folders: string[];
  // All resources
  // key: resource name for display, without "~" prefix
  // value: ResourceRow
  resources: Record<string, ResourceRow>;
  // All assets
  // key: button_name, e.g. BATTLE_PREPARATION
  // value: MetaAsset
  assets: Record<string, MetaAsset>;
}

// Extended resource information used internally by components.
// Combines display name with ResourceRow data for easier rendering.
export interface ResourceItem extends ResourceRow {
  displayName: string;
}
