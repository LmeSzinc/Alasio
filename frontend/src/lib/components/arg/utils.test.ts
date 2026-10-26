import { describe, expect, it } from "vitest";
import { type ArgData, getDashboardPairText, isDashboardInternalArg, isDashboardPairArg } from "./utils.svelte";

describe("TestIsDashboardInternalArg", () => {
  it.each([
    ["_info", "Amount"],
    ["Value", "Amount"],
    ["Value", "Progress"],
    ["Value", "DynamicTotal"],
    ["Time", "Progress"],
    ["ServerUpdate", "Planner"],
    ["Total", "DynamicTotal"],
    ["Progress", "Planner"],
    ["Eta", "Planner"],
  ])("hides %s of %s", (argKey, dashboardType) => {
    expect(isDashboardInternalArg(argKey, dashboardType)).toBe(true);
  });

  it.each([
    ["Total", "Total"],
    ["Total", "Amount"],
    ["Value", "Planner"],
    ["Progress", "Progress"],
    ["Progress", "Amount"],
    ["Eta", "Progress"],
    ["GreenValue", "Planner"],
    ["GreenTotal", "Planner"],
  ])("keeps %s of %s visible", (argKey, dashboardType) => {
    expect(isDashboardInternalArg(argKey, dashboardType)).toBe(false);
  });
});

function pairArg(value: any): ArgData {
  return { task: "Dashboard", group: "Planner", arg: "Arg", dt: "input-int", value };
}

describe("TestIsDashboardPairArg", () => {
  it("accepts both sides of a complete pair", () => {
    const group = { GreenValue: pairArg(8), GreenTotal: pairArg(10) };
    expect(isDashboardPairArg("GreenValue", group)).toBe(true);
    expect(isDashboardPairArg("GreenTotal", group)).toBe(true);
  });

  it("rejects unpaired args", () => {
    expect(isDashboardPairArg("GreenValue", { GreenValue: pairArg(8) })).toBe(false);
    expect(isDashboardPairArg("GreenTotal", { GreenTotal: pairArg(10) })).toBe(false);
  });

  it("rejects non-pair args", () => {
    expect(isDashboardPairArg("Custom", { Custom: pairArg(1) })).toBe(false);
    expect(isDashboardPairArg("Progress", { Progress: pairArg(80) })).toBe(false);
  });
});

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
