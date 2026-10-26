/**
 * Tests for the Progress and Planner value forms of PrettyValue.svelte.
 *
 * Progress renders a percentage; Planner renders a percentage followed by the
 * ETA. Both are dashboard types provided by the backend (DashboardProgress and
 * DashboardPlanner).
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

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
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

  it("renders NaN when the ETA arg is missing", async () => {
    const data = plannerData(98.22, "");
    delete data.Eta;
    const target = await mountValue(data);
    expect(target.textContent).toContain("98.22%");
    expect(target.textContent).toContain(">NaN");
  });
});
