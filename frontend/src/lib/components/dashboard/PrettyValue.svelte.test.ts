/**
 * Tests for the Progress, Planner and SoftTotal value forms of PrettyValue.svelte.
 *
 * Progress renders a percentage; Planner renders a percentage followed by the
 * ETA; SoftTotal renders the value over the dynamic soft total, which the value
 * may pass, and never guesses a missing half ("NaN / NaN"). They are dashboard
 * types provided by the backend (DashboardProgress, DashboardPlanner and
 * DashboardSoftTotal).
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import type { ArgData } from "../arg/utils.svelte";
import PrettyValue from "./PrettyValue.svelte";

let mounted: any[] = [];

/** Mounts PrettyValue over the given dashboard group and returns its target. */
async function mountValue(data: Record<string, ArgData>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(PrettyValue, { target, props: { data } }));
  await flushEffects();
  return target;
}

function progressData(value: number | string): Record<string, ArgData> {
  return {
    _info: {
      task: "Dashboard",
      group: "Progress",
      arg: "_info",
      dt: "dashboard-progress",
      dashboard: "Progress",
      value: "",
    },
    Value: { task: "Dashboard", group: "Progress", arg: "Value", dt: "input-float", value },
  };
}

function plannerData(progress: number | string, eta: string): Record<string, ArgData> {
  return {
    _info: {
      task: "Dashboard",
      group: "Planner",
      arg: "_info",
      dt: "dashboard-planner",
      dashboard: "Planner",
      value: "",
    },
    Progress: { task: "Dashboard", group: "Planner", arg: "Progress", dt: "input-int", value: progress },
    Eta: { task: "Dashboard", group: "Planner", arg: "Eta", dt: "input", value: eta },
  };
}

function softTotalData(value: number, total: number): Record<string, ArgData> {
  return {
    _info: {
      task: "Dashboard",
      group: "SoftTotal",
      arg: "_info",
      dt: "dashboard-total",
      dashboard: "SoftTotal",
      value: "",
    },
    Value: { task: "Dashboard", group: "SoftTotal", arg: "Value", dt: "input-int", value, ge: 0, le: 25000 },
    Total: { task: "Dashboard", group: "SoftTotal", arg: "Total", dt: "static", value: total, ge: 0, le: 25000 },
  };
}

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestPrettyValueSoftTotal", () => {
  it("renders the soft total as the denominator, not the hard total", async () => {
    const target = await mountValue(softTotalData(12345, 23145));
    expect(target.textContent).toContain("12345");
    expect(target.textContent).toContain("/ 23145");
    expect(target.textContent).not.toContain("25000");
  });

  it("keeps a value that passed the soft total", async () => {
    const target = await mountValue(softTotalData(24000, 23145));
    expect(target.textContent).toContain("24000");
    expect(target.textContent).toContain("/ 23145");
  });

  it("renders NaN for both halves when the value is missing", async () => {
    // Nothing is guessed: a pair without a value is unknown as a whole, even
    // though the total row was delivered
    const data = softTotalData(12345, 23145);
    delete data.Value;
    const target = await mountValue(data);
    expect(target.textContent.replace(/\s+/g, " ")).toContain("NaN / NaN");
  });

  it("renders NaN for both halves when neither half was written", async () => {
    const data = softTotalData(0, 0);
    delete data.Value;
    delete data.Total;
    const target = await mountValue(data);
    expect(target.textContent.replace(/\s+/g, " ")).toContain("NaN / NaN");
    expect(target.textContent.replace(/\s+/g, " ")).not.toContain("0 /");
  });

  it("renders NaN for a missing soft total and keeps the value", async () => {
    const data = softTotalData(12345, 0);
    delete data.Total;
    const target = await mountValue(data);
    expect(target.textContent.replace(/\s+/g, " ")).toContain("12345 / NaN");
  });
});

describe("TestPrettyValueProgress", () => {
  it("renders the value as a percentage", async () => {
    const target = await mountValue(progressData(86.54));
    expect(target.textContent).toContain("86.54%");
  });

  it("clamps the percentage to 0-100", async () => {
    const target = await mountValue(progressData(150));
    expect(target.textContent).toContain("100.00%");
  });

  it("renders NaN when the progress value is missing", async () => {
    // 0.00% would claim the progress is empty instead of unknown
    const data = progressData(45.67);
    delete data.Value;
    const target = await mountValue(data);
    expect(target.textContent.replace(/\s+/g, " ")).toContain("NaN%");
    expect(target.textContent).not.toContain("0.00%");
  });
});

describe("TestPrettyValuePlanner", () => {
  it("renders progress and ETA", async () => {
    const target = await mountValue(plannerData(56.27, "3.6d"));
    expect(target.textContent).toContain("56.27%");
    expect(target.textContent).toContain(">3.6d");
  });

  it("renders NaN when the ETA is empty", async () => {
    const target = await mountValue(plannerData(98.22, ""));
    expect(target.textContent).toContain("98.22%");
    expect(target.textContent).toContain(">NaN");
  });

  it("renders NaN for a missing progress and keeps the ETA", async () => {
    const data = plannerData(56.27, "3.6d");
    delete data.Progress;
    const target = await mountValue(data);
    expect(target.textContent.replace(/\s+/g, " ")).toContain("NaN%");
    expect(target.textContent).toContain(">3.6d");
  });

  it("renders NaN when the ETA arg is missing", async () => {
    const data = plannerData(98.22, "");
    delete data.Eta;
    const target = await mountValue(data);
    expect(target.textContent).toContain("98.22%");
    expect(target.textContent).toContain(">NaN");
  });
});
