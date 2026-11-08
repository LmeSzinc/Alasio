import { describe, expect, it } from "vitest";
import { filterTree } from "./tree";
import type { GoldenTestEntry } from "./types";

function entry(path: string, isDir: boolean): GoldenTestEntry {
  const name = path.slice(path.lastIndexOf("/") + 1);
  return { name, path, is_dir: isDir };
}

/** The visible paths per folder, for a compact assertion */
function shape(tree: Record<string, GoldenTestEntry[]>): Record<string, string[]> {
  return Object.fromEntries(Object.entries(tree).map(([folder, entries]) => [folder, entries.map((e) => e.path)]));
}

const DIRS: Record<string, GoldenTestEntry[]> = {
  "": [entry("map", true), entry("test_screenshot.py", false)],
  map: [entry("map/fleet", true), entry("map/test_fleet.py", false)],
  "map/fleet": [entry("map/fleet/test_bar.py", false)],
};

describe("TestFilterTree", () => {
  it("keeps the tree untouched without a query", () => {
    expect(filterTree(DIRS, "")).toBe(DIRS);
    expect(filterTree(DIRS, "   ")).toBe(DIRS);
  });

  it("keeps a matching file and its ancestors", () => {
    expect(shape(filterTree(DIRS, "test_bar"))).toEqual({
      "": ["map"],
      map: ["map/fleet"],
      "map/fleet": ["map/fleet/test_bar.py"],
    });
  });

  it("matches a part of the path, case insensitively", () => {
    expect(shape(filterTree(DIRS, "FLEET"))).toEqual({
      "": ["map"],
      map: ["map/fleet", "map/test_fleet.py"],
      "map/fleet": ["map/fleet/test_bar.py"],
    });
  });

  it("keeps the loaded children of a matching folder", () => {
    expect(shape(filterTree(DIRS, "map/fleet"))).toEqual({
      "": ["map"],
      map: ["map/fleet"],
      "map/fleet": ["map/fleet/test_bar.py"],
    });
  });

  it("drops the folders the query misses", () => {
    expect(shape(filterTree(DIRS, "test_screenshot"))).toEqual({
      "": ["test_screenshot.py"],
      map: [],
      "map/fleet": [],
    });
  });

  it("returns empty levels when nothing matches", () => {
    expect(shape(filterTree(DIRS, "nothing"))).toEqual({ "": [], map: [], "map/fleet": [] });
  });
});
