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
import {
  type PillBox,
  appendItem,
  composeOption,
  cutPills,
  defaultItem,
  duplicateIndexes,
  insertRow,
  moveItem,
  moveRow,
  primaryOf,
  removeAt,
  removeItem,
  sameList,
  splitOption,
  validItems,
} from "./filterOrder";

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

describe("TestDuplicateIndexes", () => {
  it("marks the later occurrences of an item, not the first one", () => {
    // The first occurrence is where the item belongs, every later one is the
    // leftover that has to be dropped
    expect([...duplicateIndexes(["a", "b", "a", "c", "b"])]).toEqual([2, 4]);
  });

  it("clears the mark of an item whose first occurrence is removed", () => {
    // Deleting the first row must leave the later one legal, not keep it marked
    const rest = removeAt(["a", "b", "a"], 0);

    expect(rest).toEqual(["b", "a"]);
    expect(duplicateIndexes(rest).size).toBe(0);
  });

  it.each([{ list: [] as string[] }, { list: ["a"] }, { list: ["a", "b", "c"] }])(
    "finds no duplicate in $list",
    ({ list }) => {
      expect(duplicateIndexes(list).size).toBe(0);
    },
  );

  it("marks every occurrence after the first", () => {
    expect([...duplicateIndexes(["a", "a", "a"])]).toEqual([1, 2]);
  });

  it("marks a later occurrence that is not adjacent to the first one", () => {
    expect([...duplicateIndexes(["a", "b", "c", "a"])]).toEqual([3]);
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

describe("TestRemoveAt", () => {
  it("removes the row at the index, not every row of that value", () => {
    // A repeated item has one valid occurrence, a delete must keep it
    expect(removeAt(["a", "b", "a"], 2)).toEqual(["a", "b"]);
    expect(removeAt(["a", "b", "a"], 0)).toEqual(["b", "a"]);
  });

  it.each([{ index: -1 }, { index: 3 }])("is a no-op for the out of range index $index", ({ index }) => {
    expect(removeAt(["a", "b"], index)).toEqual(["a", "b"]);
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

describe("TestInsertRow", () => {
  it("inserts an item before / after a selected row", () => {
    expect(insertRow(["a", "b"], "c", { kind: "selected", index: 0, position: "top" })).toEqual(["c", "a", "b"]);
    expect(insertRow(["a", "b"], "c", { kind: "selected", index: 0, position: "bottom" })).toEqual(["a", "c", "b"]);
    expect(insertRow(["a", "b"], "c", { kind: "selected", index: 1, position: "bottom" })).toEqual(["a", "b", "c"]);
  });

  it("drops on the column: the top area inserts first, the rest appends last", () => {
    expect(insertRow(["a", "b"], "c", { kind: "selected", position: "top" })).toEqual(["c", "a", "b"]);
    expect(insertRow(["a", "b"], "c", { kind: "selected", position: "bottom" })).toEqual(["a", "b", "c"]);
    expect(insertRow([], "c", { kind: "selected", position: "bottom" })).toEqual(["c"]);
  });

  it("leaves the order alone when the row is dropped on the unused column", () => {
    expect(insertRow(["a"], "c", { kind: "unused" })).toEqual(["a"]);
  });

  it("clamps an index outside the list", () => {
    expect(insertRow(["a"], "c", { kind: "selected", index: 9, position: "bottom" })).toEqual(["a", "c"]);
    expect(insertRow(["a"], "c", { kind: "selected", index: -3, position: "top" })).toEqual(["c", "a"]);
  });
});

describe("TestMoveRow", () => {
  it("reorders a row around another row", () => {
    // Moving down over a later row inserts after it
    expect(moveRow(["a", "b", "c"], 0, { kind: "selected", index: 2, position: "bottom" })).toEqual(["b", "c", "a"]);
    // Moving up over an earlier row inserts before it
    expect(moveRow(["a", "b", "c"], 2, { kind: "selected", index: 0, position: "top" })).toEqual(["c", "a", "b"]);
  });

  it("drops on the column: the top area inserts first, the rest appends last", () => {
    expect(moveRow(["a", "b", "c"], 0, { kind: "selected", position: "bottom" })).toEqual(["b", "c", "a"]);
    expect(moveRow(["a", "b", "c"], 2, { kind: "selected", position: "top" })).toEqual(["c", "a", "b"]);
  });

  it("drops on the unused column: the row only leaves the order", () => {
    expect(moveRow(["a", "b"], 0, { kind: "unused" })).toEqual(["b"]);
  });

  it("drops a row on its own position without moving it", () => {
    expect(moveRow(["a", "b"], 0, { kind: "selected", index: 0, position: "top" })).toEqual(["a", "b"]);
    expect(moveRow(["a", "b"], 0, { kind: "selected", index: 0, position: "bottom" })).toEqual(["a", "b"]);
  });

  it("is a no-op for an index outside the list", () => {
    expect(moveRow(["a"], 3, { kind: "selected", position: "top" })).toEqual(["a"]);
    expect(moveRow(["a"], -1, { kind: "unused" })).toEqual(["a"]);
  });

  it("moves only the dragged row of a repeated value", () => {
    // Two rows hold "a"; dragging the second one to the front must not take the
    // first one along, they are different rows of the same value
    expect(moveRow(["a", "b", "a"], 2, { kind: "selected", index: 0, position: "top" })).toEqual(["a", "a", "b"]);
    // ... and dragging the first one to the end keeps the other one in place
    expect(moveRow(["a", "b", "a"], 0, { kind: "selected", index: 2, position: "bottom" })).toEqual(["b", "a", "a"]);
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

describe("TestSplitOption", () => {
  it.each([
    ["PlateT4-gun", ["PlateT4", "gun"]],
    ["Chip", ["Chip", null]],
    // split on the first separator only
    ["PlateT4-gun-x", ["PlateT4", "gun-x"]],
    ["Chip-", ["Chip", ""]],
  ])("splits %s", (item, expected) => {
    expect(splitOption(item)).toEqual(expected);
  });

  it("reads the primary item of both kinds of item", () => {
    expect(primaryOf("PlateT4-gun")).toBe("PlateT4");
    expect(primaryOf("Chip")).toBe("Chip");
  });
});

describe("TestComposeOption", () => {
  it("joins a primary item with its secondary option", () => {
    expect(composeOption("PlateT4", "gun")).toBe("PlateT4-gun");
  });

  it.each([null, undefined, ""])("keeps the bare item for %s", (secondary) => {
    expect(composeOption("Chip", secondary)).toBe("Chip");
  });

  it("round-trips an item", () => {
    const [primary, secondary] = splitOption("PlateT4-gun");
    expect(composeOption(primary, secondary)).toBe("PlateT4-gun");
  });
});

describe("TestValidItems", () => {
  it("lists the options themselves without secondary options", () => {
    expect([...validItems(["Fleet-1", "Fleet-2"], null, "filter-order", null)]).toEqual(["Fleet-1", "Fleet-2"]);
  });

  it("ignores the option dictionary of a one level datatype", () => {
    // The dt alone decides the shape, a leftover option_dict changes nothing
    expect([...validItems(["Fleet-1"], { "Fleet-1": ["a"] }, "filter-order", null)]).toEqual(["Fleet-1"]);
  });

  it("expands a primary item into its own secondary options only", () => {
    const valid = validItems(
      ["PlateT4", "BookT3", "Chip"],
      {
        PlateT4: ["gun", "general"],
        BookT3: ["red"],
      },
      "filter-order-item",
      null,
    );

    expect([...valid]).toEqual(["PlateT4-gun", "PlateT4-general", "BookT3-red", "Chip"]);
    // a secondary option of another item is not a legal item of this one
    expect(valid.has("PlateT4-red")).toBe(false);
    expect(valid.has("Chip-gun")).toBe(false);
  });

  it("expands the pairs of a filter-order-pair the same way", () => {
    const valid = validItems(["BookT3"], { BookT3: ["red", "blue"] }, "filter-order-pair", null);

    expect([...valid]).toEqual(["BookT3-red", "BookT3-blue"]);
  });

  it("includes the optionCommand of a filter-order-pair", () => {
    // A command is stored as itself, so a value holding it is not invalid
    const valid = validItems(["Major"], null, "filter-order-pair", ["shortest"]);

    expect([...valid]).toEqual(["Major", "shortest"]);
  });

  it("leaves an undeclared command out", () => {
    const valid = validItems(["Major"], null, "filter-order-pair", []);

    expect(valid.has("shortest")).toBe(false);
  });

  it("ignores optionCommand of a datatype that has none", () => {
    const valid = validItems(["Fleet-1"], null, "filter-order", ["shortest"]);

    expect([...valid]).toEqual(["Fleet-1"]);
  });

  it("requires the secondary option of a primary that declares one", () => {
    const valid = validItems(["PlateT4"], { PlateT4: ["gun"] }, "filter-order-item", null);

    expect(valid.has("PlateT4")).toBe(false);
  });

  it("accepts the items of the legacy filter-order as str", () => {
    expect([...validItems([1, 2], null, "filter-order", null)]).toEqual(["1", "2"]);
  });
});

describe("TestDefaultItem", () => {
  it("starts an unused primary at its first secondary option", () => {
    expect(defaultItem("PlateT4", { PlateT4: ["gun", "general"] })).toBe("PlateT4-gun");
  });

  it("adds a primary without secondary options as itself", () => {
    expect(defaultItem("Chip", { PlateT4: ["gun"] })).toBe("Chip");
    expect(defaultItem("Chip")).toBe("Chip");
  });
});
