import fs from "node:fs";
import path from "node:path";

/**
 * Temporary suffix appended to route files during build, e.g.
 * `+page.svelte` -> `+page.svelte.dropped`.
 *
 * `svelte-kit sync` only discovers route convention files, so a dropped
 * route file is invisible to the route scan and the route is completely
 * absent from the build output (no node, no chunk, no placeholder).
 */
export const DROPPED_SUFFIX = ".dropped";

/**
 * SvelteKit route convention files. Only these files can be dropped to
 * remove a route from the route table; dropping a regular component
 * would break its importers.
 */
export const ROUTE_FILES = new Set([
  "+page.svelte",
  "+page.ts",
  "+page.server.ts",
  "+layout.svelte",
  "+layout.ts",
  "+layout.server.ts",
  "+error.svelte",
  "+server.ts",
]);

/** Recursively collect all files under dir, returns absolute paths. */
function walkFiles(dir: string, out: string[] = []): string[] {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (error) {
    // Routes directory may not exist yet
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return out;
    throw error;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkFiles(full, out);
    } else if (entry.isFile()) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Rename marked route files to the temporary dropped suffix, so the
 * routes disappear from the next `svelte-kit sync` scan.
 *
 * Idempotent for interrupted builds: files already dropped are kept
 * as-is, and the remaining marked files are dropped to complete the
 * interrupted run.
 *
 * Args:
 *     routesDir (str): Absolute path of the routes directory
 *     marker (str): Marker comment that marks a route file to drop
 *
 * Returns:
 *     list[str]: Absolute paths of the files renamed in this call
 */
export function dropMarkedRoutes(routesDir: string, marker: string): string[] {
  const dropped: string[] = [];
  for (const file of walkFiles(routesDir)) {
    const basename = path.basename(file);
    // Already dropped by an interrupted build, keep it dropped
    if (basename.endsWith(DROPPED_SUFFIX)) continue;
    // Only route convention files can be dropped
    if (!ROUTE_FILES.has(basename)) continue;
    let content;
    try {
      content = fs.readFileSync(file, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
    if (!content.includes(marker)) continue;
    try {
      fs.renameSync(file, file + DROPPED_SUFFIX);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(`svelte-drop-dev-page: failed to drop route file ${file}: ${message}`);
    }
    dropped.push(file);
  }
  return dropped;
}

/**
 * Restore route files that were renamed to the temporary dropped suffix.
 *
 * Called after a build finishes and when a dev server starts (to repair
 * the residue of an interrupted build). Only files whose original name
 * is a route convention file are restored, so unrelated files that
 * happen to end with the dropped suffix are left untouched.
 *
 * Args:
 *     routesDir (str): Absolute path of the routes directory
 *
 * Returns:
 *     list[str]: Absolute paths of the restored files
 */
export function restoreDroppedRoutes(routesDir: string): string[] {
  const restored: string[] = [];
  for (const file of walkFiles(routesDir)) {
    if (!file.endsWith(DROPPED_SUFFIX)) continue;
    const original = file.slice(0, -DROPPED_SUFFIX.length);
    // Not a route convention file, not one of ours
    if (!ROUTE_FILES.has(path.basename(original))) continue;
    try {
      fs.renameSync(file, original);
    } catch (error) {
      // Target may already exist, or the file is locked; leave it and warn
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[svelte-drop-dev-page] failed to restore ${file}: ${message}`);
      continue;
    }
    restored.push(original);
  }
  return restored;
}
