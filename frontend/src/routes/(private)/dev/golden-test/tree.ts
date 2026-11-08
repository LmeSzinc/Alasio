/**
 * Filtering of the loaded part of the test tree.
 *
 * The tree arrives one folder level at a time, so a filter can only see the
 * levels the page has opened. That is the useful behavior anyway: the filter
 * narrows what is on screen, it never claims to know about a folder that was
 * never read.
 */
import type { GoldenTestEntry } from "./types";

/**
 * Keep the entries of the loaded tree that match a query.
 *
 * An entry matches when its path contains the query, case insensitively. The
 * ancestors of a match stay, so the path of the match is readable, and every
 * loaded child of a matching folder stays as well: a folder that matches is
 * shown with its content, not as an empty node.
 *
 * @param dirs Folder path relative to tests/ -> its immediate children
 * @param query The text typed in the filter box
 * @returns The same shape, narrowed to the matching entries; the input itself
 *     when the query is empty
 */
export function filterTree(dirs: Record<string, GoldenTestEntry[]>, query: string): Record<string, GoldenTestEntry[]> {
  const needle = query.trim().toLowerCase();
  if (needle === "") return dirs;

  const visible = new Set<string>();

  const keepAll = (folder: string) => {
    for (const entry of dirs[folder] ?? []) {
      visible.add(entry.path);
      if (entry.is_dir === true) keepAll(entry.path);
    }
  };

  const visit = (folder: string): boolean => {
    let matched = false;
    for (const entry of dirs[folder] ?? []) {
      const below = entry.is_dir === true ? visit(entry.path) : false;
      const self = entry.path.toLowerCase().includes(needle);
      if (!self && !below) continue;
      visible.add(entry.path);
      if (self && entry.is_dir === true) keepAll(entry.path);
      matched = true;
    }
    return matched;
  };
  visit("");

  const kept: Record<string, GoldenTestEntry[]> = {};
  for (const [folder, entries] of Object.entries(dirs)) {
    kept[folder] = entries.filter((entry) => visible.has(entry.path));
  }
  return kept;
}
