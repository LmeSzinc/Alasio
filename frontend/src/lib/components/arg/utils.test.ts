import { describe, expect, it } from "vitest";
import {
  type ArgData,
  type InfoData,
  getArgName,
  getDashboardPairText,
  i18nTextToLines,
  i18nTextToOneLine,
} from "./utils.svelte";

function pairArg(value: any): ArgData {
  return { task: "Dashboard", group: "Planner", arg: "Arg", dt: "input-int", value };
}

describe("TestGetDashboardPairText", () => {
  it("returns empty without a complete pair", () => {
    expect(getDashboardPairText({})).toBe("");
    expect(getDashboardPairText({ Value: pairArg(8) })).toBe("");
    expect(getDashboardPairText({ Total: pairArg(10) })).toBe("");
  });

  it("renders one pair", () => {
    expect(
      getDashboardPairText({
        GreenValue: pairArg(8),
        GreenTotal: pairArg(10),
      }),
    ).toBe("8 / 10");
  });

  it("renders multiple pairs in Value order", () => {
    expect(
      getDashboardPairText({
        GreenValue: pairArg(33),
        GreenTotal: pairArg(33),
        BlueValue: pairArg(22),
        BlueTotal: pairArg(44),
      }),
    ).toBe("(33, 22) / (33, 44)");
  });

  it("keeps the Value order when the pairs are interleaved", () => {
    expect(
      getDashboardPairText({
        GreenValue: pairArg(33),
        BlueTotal: pairArg(44),
        BlueValue: pairArg(22),
        GreenTotal: pairArg(33),
      }),
    ).toBe("(33, 22) / (33, 44)");
  });

  it("ignores unpaired args next to a complete pair", () => {
    expect(
      getDashboardPairText({
        Value: pairArg(8),
        Total: pairArg(10),
        SoloValue: pairArg(1),
        OrphanTotal: pairArg(2),
      }),
    ).toBe("8 / 10");
  });
});

function namedArg(name: string | string[] | undefined): ArgData {
  return { task: "Task", group: "Group", arg: "Mode", dt: "input", value: "", name };
}

describe("TestGetArgName", () => {
  it("keeps a name of one line and a name of multiple lines as is", () => {
    expect(getArgName(namedArg("The mode"))).toBe("The mode");
    expect(getArgName(namedArg(["The mode", "of the task"]))).toEqual(["The mode", "of the task"]);
  });

  it("falls back to {group}.{arg} without a name", () => {
    expect(getArgName(namedArg(undefined))).toBe("Group.Mode");
    expect(getArgName({ group: "", arg: "_info", card: "card" } as InfoData)).toBe("<UnknownGroup>._info");
  });
});

describe("TestI18nTextToLines", () => {
  it("keeps a text of one line as one row", () => {
    expect(i18nTextToLines("The mode")).toEqual(["The mode"]);
  });

  it("keeps the rows of a text of multiple lines", () => {
    expect(i18nTextToLines(["The mode", "of the task"])).toEqual(["The mode", "of the task"]);
  });

  it("returns no row for a missing or empty text", () => {
    expect(i18nTextToLines(undefined)).toEqual([]);
    expect(i18nTextToLines(null)).toEqual([]);
    expect(i18nTextToLines("")).toEqual([]);
    expect(i18nTextToLines([])).toEqual([]);
  });
});

describe("TestI18nTextToOneLine", () => {
  it("joins the lines of a text with a space", () => {
    expect(i18nTextToOneLine(["The mode", "of the task"])).toBe("The mode of the task");
  });

  it("keeps a text of one line as is", () => {
    expect(i18nTextToOneLine("The mode")).toBe("The mode");
  });

  it("returns an empty str for a missing or empty text", () => {
    expect(i18nTextToOneLine(undefined)).toBe("");
    expect(i18nTextToOneLine(null)).toBe("");
    expect(i18nTextToOneLine("")).toBe("");
    expect(i18nTextToOneLine([])).toBe("");
  });
});
