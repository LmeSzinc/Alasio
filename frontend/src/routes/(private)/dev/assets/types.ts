// Type definitions mirroring backend Python models.
// These interfaces represent the data structures returned by the backend API.

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
  mod_path_assets: string
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
  // value: AssetData
  assets: Record<string, unknown>;
}

// Extended resource information used internally by components.
// Combines display name with ResourceRow data for easier rendering.
export interface ResourceItem extends ResourceRow {
  displayName: string;
}
