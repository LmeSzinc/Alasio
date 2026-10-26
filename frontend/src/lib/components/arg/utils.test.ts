import { describe, expect, it } from "vitest";
import { isDashboardInternalArg } from "./utils.svelte";

describe("TestIsDashboardInternalArg", () => {
  it.each([
    ["_info", "Amount"],
    ["Value", "Amount"],
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
    ["Progress", "Progress"],
    ["Progress", "Amount"],
    ["Eta", "Progress"],
    ["GreenValue", "Planner"],
    ["GreenTotal", "Planner"],
  ])("keeps %s of %s visible", (argKey, dashboardType) => {
    expect(isDashboardInternalArg(argKey, dashboardType)).toBe(false);
  });
});
