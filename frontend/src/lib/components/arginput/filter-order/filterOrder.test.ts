/**
 * Tests of the pure helpers of dt="filter-order" (filterOrder.ts)
 *
 * The dialog keeps every list operation in these functions, so the drop math
 * (cross-column moves, same-list reordering, dropping on a column) is covered
 * here instead of through the dnd gestures, which jsdom cannot drive.
 *
 * Note the file name: on a case insensitive file system "FilterOrder.test.ts"
 * would be the same file as "filterOrder.test.ts", so the component test of the
 * same component is "FilterOrder.svelte.test.ts".
 */
import { describe, expect, it } from "vitest";
import { appendItem, applyDrop, moveItem, removeItem, sameList } from "./filterOrder";

describe("TestSameList", () => {
  it("compares the items and the order", () => {
    expect(sameList([], [])).toBe(true);
    expect(sameList(["a", "b"], ["a", "b"])).toBe(true);
    expect(sameList(["a", "b"], ["a"])).toBe(false);
    expect(sameList(["a"], ["a", "b"])).toBe(false);
    // The order is part of the value
    expect(sameList(["a", "b"], ["b", "a"])).toBe(false);
  });
});

describe("TestMoveItem", () => {
  it("moves an item up and down", () => {
    expect(moveItem(["a", "b", "c"], 2, -1)).toEqual(["a", "c", "b"]);
    expect(moveItem(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
  });

  it("is a no-op when the move leaves the list", () => {
    // The first item cannot move up, the last one cannot move down
    expect(moveItem(["a", "b"], 0, -1)).toEqual(["a", "b"]);
    expect(moveItem(["a", "b"], 1, 1)).toEqual(["a", "b"]);
    // An out of range index must not break the call
    expect(moveItem(["a", "b"], -1, 1)).toEqual(["a", "b"]);
    expect(moveItem(["a", "b"], 2, -1)).toEqual(["a", "b"]);
    expect(moveItem([], 0, 1)).toEqual([]);
  });

  it("returns a copy, the input stays untouched", () => {
    const list = ["a", "b"];
    const next = moveItem(list, 0, 1);
    expect(next).not.toBe(list);
    expect(list).toEqual(["a", "b"]);
  });
});

describe("TestAppendItem", () => {
  it("appends at the end", () => {
    expect(appendItem([], "a")).toEqual(["a"]);
    expect(appendItem(["a"], "b")).toEqual(["a", "b"]);
  });

  it("never adds an item twice", () => {
    expect(appendItem(["a"], "a")).toEqual(["a"]);
  });
});

describe("TestRemoveItem", () => {
  it("removes the item", () => {
    expect(removeItem(["a", "b", "c"], "b")).toEqual(["a", "c"]);
  });

  it("is a no-op for a missing item", () => {
    expect(removeItem(["a"], "b")).toEqual(["a"]);
    expect(removeItem([], "a")).toEqual([]);
  });
});

describe("TestApplyDrop", () => {
  it("inserts an unused item before / after a selected row", () => {
    expect(applyDrop(["a", "b"], "c", { kind: "selected", itemId: "a", position: "top" })).toEqual(["c", "a", "b"]);
    expect(applyDrop(["a", "b"], "c", { kind: "selected", itemId: "a", position: "bottom" })).toEqual(["a", "c", "b"]);
    expect(applyDrop(["a", "b"], "c", { kind: "selected", itemId: "b", position: "bottom" })).toEqual(["a", "b", "c"]);
  });

  it("reorders a selected item around another row", () => {
    // Moving down over a later row inserts after it
    expect(applyDrop(["a", "b", "c"], "a", { kind: "selected", itemId: "c", position: "bottom" })).toEqual([
      "b",
      "c",
      "a",
    ]);
    // Moving up over an earlier row inserts before it
    expect(applyDrop(["a", "b", "c"], "c", { kind: "selected", itemId: "a", position: "top" })).toEqual([
      "c",
      "a",
      "b",
    ]);
  });

  it("drops on the column: the top area inserts first, the rest appends last", () => {
    expect(applyDrop(["a", "b"], "c", { kind: "selected", position: "top" })).toEqual(["c", "a", "b"]);
    expect(applyDrop(["a", "b"], "c", { kind: "selected", position: "bottom" })).toEqual(["a", "b", "c"]);
    // Dropping a selected item on the column bottom moves it to the end
    expect(applyDrop(["a", "b", "c"], "a", { kind: "selected", position: "bottom" })).toEqual(["b", "c", "a"]);
  });

  it("drops on the unused column: the item only leaves the order", () => {
    expect(applyDrop(["a", "b"], "a", { kind: "unused" })).toEqual(["b"]);
    // An item that is not in the order stays out of it
    expect(applyDrop(["a"], "b", { kind: "unused" })).toEqual(["a"]);
  });

  it("drops an item on its own row without moving it", () => {
    expect(applyDrop(["a", "b"], "a", { kind: "selected", itemId: "a", position: "top" })).toEqual(["a", "b"]);
    expect(applyDrop(["a", "b"], "a", { kind: "selected", itemId: "a", position: "bottom" })).toEqual(["a", "b"]);
  });

  it("appends when the target row is not in the order", () => {
    expect(applyDrop(["a"], "b", { kind: "selected", itemId: "gone", position: "top" })).toEqual(["a", "b"]);
  });
});
