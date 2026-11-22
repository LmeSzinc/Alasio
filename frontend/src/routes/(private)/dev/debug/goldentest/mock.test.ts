/**
 * Tests for the mock data of the golden test review demo.
 *
 * The demo only looks like the real page when the mock keeps the relationship
 * the backend guarantees: a tree and the files it names, a source whose line
 * numbers are the anchors of the images, and bindings that point at an actual
 * `@image` decorator. These tests hold that relationship, so editing a source
 * without moving its numbers fails here instead of in the page.
 */
import { describe, expect, it } from "vitest";
import type { GoldenTestFileData, UnittestImage } from "$private/dev/golden-test/types";
import { DEMO_FILES, DEMO_MOD, DEMO_SELECTED, DEMO_TREE } from "./mock";

/** The lines of a source, as the page counts them */
function lines(source: string): string[] {
  return source.split("\n");
}

/** The bindings of a mock file, with the defaults the page fills in */
function bindings(data: GoldenTestFileData): Required<UnittestImage>[] {
  return (data.images ?? []).map((image) => ({
    class_name: image.class_name ?? "",
    func_name: image.func_name ?? "",
    start_line: image.start_line ?? 0,
    end_line: image.end_line ?? 0,
    image: image.image ?? "",
    error: image.error ?? "",
  }));
}

/**
 * Last line of a test function.
 *
 * The backend reports the last line of the function: the last line with content
 * before the next binding, before the class that opens the next block, or the
 * end of the file.
 */
function expectedEnd(source: string[], startLine: number, limitLine: number): number {
  for (let line = limitLine - 1; line >= startLine; line--) {
    const text = source[line - 1] ?? "";
    // A blank line and the header of the next block belong to no test
    if (text.trim() === "" || text.startsWith("class ") || text.startsWith("def ")) continue;
    return line;
  }
  return startLine;
}

describe("TestDemoTree", () => {
  it("lists every entry under the folder key of its parent", () => {
    for (const [folder, entries] of Object.entries(DEMO_TREE)) {
      for (const entry of entries) {
        const parent = entry.path.includes("/") ? entry.path.slice(0, entry.path.lastIndexOf("/")) : "";
        expect(parent).toBe(folder);
      }
    }
  });

  it("gives every folder in the tree its own loaded level", () => {
    for (const entries of Object.values(DEMO_TREE)) {
      for (const entry of entries) {
        if (entry.is_dir !== true) continue;
        expect(DEMO_TREE[entry.path]).toBeDefined();
      }
    }
  });

  it("has the data of every file of the tree and no file outside it", () => {
    const files = Object.values(DEMO_TREE)
      .flat()
      .filter((entry) => entry.is_dir !== true)
      .map((entry) => entry.path);

    expect(files.toSorted()).toEqual(Object.keys(DEMO_FILES).toSorted());
  });

  it("opens with a file of the tree", () => {
    const files = Object.values(DEMO_TREE)
      .flat()
      .filter((entry) => entry.is_dir !== true)
      .map((entry) => entry.path);

    expect(files).toContain(DEMO_SELECTED);
  });
});

describe("TestDemoFiles", () => {
  it("sends every file under its own path and the demo mod", () => {
    for (const [path, data] of Object.entries(DEMO_FILES)) {
      expect(data.file).toBe(path);
      expect(data.mod_name).toBe(DEMO_MOD);
    }
  });

  it("has a source for every file", () => {
    for (const data of Object.values(DEMO_FILES)) {
      expect((data.source ?? "").trim()).not.toBe("");
    }
  });
});

describe("TestDemoImages", () => {
  it("binds every @image decorator of every source", () => {
    for (const data of Object.values(DEMO_FILES)) {
      const decorators = lines(data.source ?? "").filter((line) => line.includes("@image(")).length;
      expect(bindings(data)).toHaveLength(decorators);
    }
  });

  it("anchors every binding on its own decorator", () => {
    for (const data of Object.values(DEMO_FILES)) {
      const source = lines(data.source ?? "");
      for (const image of bindings(data)) {
        const line = source[image.start_line - 1] ?? "";
        expect(line).toContain("@image(");
        if (image.image === "") continue;
        expect(line).toContain(image.image);
      }
    }
  });

  it("names the enclosing class and the test function of every binding", () => {
    for (const data of Object.values(DEMO_FILES)) {
      const source = lines(data.source ?? "");
      for (const image of bindings(data)) {
        expect(source[image.start_line] ?? "").toContain(`def ${image.func_name}(`);
        expect(source.slice(0, image.start_line - 1)).toContain(`class ${image.class_name}:`);
      }
    }
  });

  it("ends every binding on the last line of its test", () => {
    for (const data of Object.values(DEMO_FILES)) {
      const source = lines(data.source ?? "");
      const images = bindings(data);
      for (const [index, image] of images.entries()) {
        const limit = images[index + 1]?.start_line ?? source.length + 1;
        expect(image.end_line).toBe(expectedEnd(source, image.start_line, limit));
      }
    }
  });

  it("reports the binding without a path as an error", () => {
    for (const data of Object.values(DEMO_FILES)) {
      for (const image of bindings(data)) {
        if (image.image !== "") continue;
        expect(image.error).not.toBe("");
      }
    }
  });
});
