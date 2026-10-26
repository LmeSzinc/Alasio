/**
 * Tests for the Planner pair line of ArgCard.svelte.
 *
 * A Planner dashboard group can carry custom Value/Total pairs. The dashboard
 * block shows them under the progress/ETA line, before the timestamp, so the
 * pair summary is visible without reading the editable args below it.
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ArgCard from "./ArgCard.svelte";
import type { ArgData, CardData } from "./utils.svelte";

function arg(group: string, argName: string, dt: string, value: any, extra: Partial<ArgData> = {}): ArgData {
  return { task: "Dashboard", group, arg: argName, dt, value, ...extra };
}

function makeCard(valueArg = "GreenValue", totalArg = "GreenTotal", pair = true): CardData {
  const pairArgs: Record<string, ArgData> = pair
    ? {
        [valueArg]: arg("Planner", valueArg, "input-int", 8, { ge: 0 }),
        [totalArg]: arg("Planner", totalArg, "input-int", 10, { ge: 0 }),
      }
    : {};
  return {
    _info: { group: "Card", arg: "_info", card: "card-Dashboard-Research", name: "Card" },
    Planner: {
      _info: {
        task: "Dashboard",
        group: "Planner",
        arg: "_info",
        dt: "dashboard-planner",
        dashboard: "Planner",
        name: "Planner",
        value: "",
      },
      Progress: arg("Planner", "Progress", "input-int", 80),
      Eta: arg("Planner", "Eta", "input", "5d"),
      ...pairArgs,
      Custom: arg("Planner", "Custom", "input", "hidden"),
      Time: arg("Planner", "Time", "datetime", "2026-10-26T00:00:00Z"),
    },
  } as unknown as CardData;
}

let mounted: any[] = [];

async function mountCard(card: CardData) {
  const data = $state(card);
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(
    mount(ArgCard, {
      target,
      props: { cardData: data, parentWidth: 800, handleEdit: () => {}, handleReset: () => {} },
    }),
  );
  await flushEffects();
  return target;
}

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestArgCardPlannerPair", () => {
  it("renders a prefixed Value/Total pair under the progress line", async () => {
    const target = await mountCard(makeCard());

    expect(target.textContent).toContain("80.00%");
    expect(target.textContent).toContain(">5d");
    expect(target.textContent).toContain("8 / 10");
    expect(target.textContent).not.toContain("Planner.Custom");
  });

  it("shows no pair line or custom arg when no pair is defined", async () => {
    const target = await mountCard(makeCard("", "", false));

    expect(target.textContent).toContain("80.00%");
    expect(target.textContent).toContain(">5d");
    expect(target.textContent).not.toContain("8 / 10");
    expect(target.textContent).not.toContain("Planner.Custom");
  });

  it("renders an unprefixed Value/Total pair and both inputs", async () => {
    const target = await mountCard(makeCard("Value", "Total"));

    expect(target.textContent).toContain("8 / 10");
    expect(target.textContent).toContain("Planner.Value");
    expect(target.textContent).toContain("Planner.Total");
    expect(target.textContent).not.toContain("Planner.Custom");
  });
});
