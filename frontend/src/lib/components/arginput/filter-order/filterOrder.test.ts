/**
 * Tests of the pure helpers of dt="filter-order" (filterOrder.ts)
 *
 * The dialog keeps every list operation in these functions, so the drop math
 * (cross-column moves, same-list reordering, dropping on a column) is covered
 * here instead of through the dnd gestures, which jsdom cannot drive. For the
 * same reason the cut of the pill lines (cutPills) is covered here instead of
 * through the layout, which jsdom does not do.
 *
 * Note the file name: on a case insensitive file system "FilterOrder.test.ts"
 * would be the same file as "filterOrder.test.ts", so the component test of the
 * same component is "FilterOrder.svelte.test.ts".
 */
import { describe, expect, it } from "vitest";
import { type PillBox, appendItem, applyDrop, cutPills, moveItem, removeItem, sameList } from "./filterOrder";

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

describe("TestCutPills", () => {
  /**
   * Boxes of `total` pills of 100px, `perLine` of them per line (4px gap in
   * between), the lines 22px apart: the right edges of a line are 100, 204, ...
   */
  function boxes(total: number, perLine = 2): PillBox[] {
    return Array.from({ length: total }, (_, index) => ({
      top: Math.floor(index / perLine) * 22,
      right: (index % perLine) * 104 + 100,
    }));
  }

  it("keeps every pill that fits into the lines", () => {
    // 8 pills of 2 per line fill 4 lines
    expect(cutPills(boxes(8), 40, 4, 208, 5)).toBe(8);
    // 10 pills exactly fill the 5 lines: nothing is cut, so no marker is placed
    expect(cutPills(boxes(10), 40, 4, 208, 5)).toBe(10);
    expect(cutPills([], 40, 4, 208, 5)).toBe(0);
  });

  it("cuts at the fifth line, the marker ends the line of the last pill", () => {
    // 14 pills over 7 lines, the marker fits next to pill 10 (line 5, slot 2)
    expect(cutPills(boxes(14), 40, 4, 300, 5)).toBe(10);
    // 204 + 4 + 40 exactly reaches the edge of the line
    expect(cutPills(boxes(14), 40, 4, 248, 5)).toBe(10);
    // A line without a gap before the marker, the same pills fit
    expect(cutPills(boxes(14), 40, 0, 244, 5)).toBe(10);
  });

  it("drops pills of the last line until the marker fits", () => {
    // 204 + 4 + 40 is wider than the line, pill 10 moves behind the marker
    expect(cutPills(boxes(14), 40, 4, 247, 5)).toBe(9);
    // 100 + 4 + 40 exactly reaches the edge: pill 9 keeps it
    expect(cutPills(boxes(14), 40, 4, 144, 5)).toBe(9);
  });

  it("walks back into the line before the cut when the last line is too narrow", () => {
    // Line 5 holds a single wide pill, line 4 ends with a narrow one: the marker
    // has to move up a line to fit next to pill 4
    const geometry: PillBox[] = [
      { top: 0, right: 100 },
      { top: 22, right: 100 },
      { top: 44, right: 100 },
      { top: 66, right: 80 },
      { top: 88, right: 300 },
      { top: 110, right: 300 },
    ];
    expect(cutPills(geometry, 40, 4, 150, 5)).toBe(4);
  });

  it("never cuts away the last pill, the marker wraps below it instead", () => {
    // The marker fits next to no pill at all (100 + 4 + 40 > 120)
    expect(cutPills(boxes(6, 1), 40, 4, 120, 5)).toBe(1);
  });
});
