/**
 * Type definitions mirroring backend Python models.
 * These interfaces represent the data structures returned by the backend API.
 */

/**
 * Represents metadata about a single resource file.
 * Corresponds to ResourceRow struct in Python backend.
 */
export interface ResourceRow {
    /**
     * filename, resource filename always startswith "~"
     * e.g. ~BATTLE_PREPARATION.png, ~Screenshot_xxx.png
     */
    name: string;
    
    /**
     * whether local file exist
     * An resource file can be one of these type:
     * - tracked in resource.json and local file exists
     * - tracked in resource.json but not downloaded yet
     * - a local file not tracked in resource.json
     */
    exist: boolean;
    
    /**
     * whether file is tracked in resource.json
     */
    track: boolean;
  }
  
  /**
   * Represents a complete folder's contents from the backend.
   * Corresponds to FolderResponse struct in Python backend.
   */
  export interface FolderResponse {
    /**
     * Sub-folders, list of folder names
     */
    folders: string[];
    
    /**
     * All resources
     * key: resource name for display, without "~" prefix
     * value: ResourceRow
     */
    resources: Record<string, ResourceRow>;
    
    /**
     * All assets
     * key: button_name, e.g. BATTLE_PREPARATION
     * value: AssetData
     */
    assets: Record<string, unknown>;
  }
  
  /**
   * Extended resource information used internally by components.
   * Combines display name with ResourceRow data for easier rendering.
   */
  export interface ResourceItem extends ResourceRow {
    displayName: string;
  }