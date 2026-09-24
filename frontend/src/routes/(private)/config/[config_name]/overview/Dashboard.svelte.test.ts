/**
 * Tests for the flash of the overview dashboard when the connection switches to
 * another config.
 *
 * The Dashboard topic is re-delivered wholesale when the config changes: the
 * backend rebuilds the view of the new config and the client replaces the whole
 * tree with it (`o: "full"`), while an update of the running config arrives as
 * a keyed `o: "set"` patch of the tree the client already holds. Only such a
 * patch carries values that were updated under the item; a full carries the
 * values of something else, so switching config must not light up the dashboard.
 *
 * The test drives the real pipeline: the singleton ws client `useTopic` uses,
 * fed by FakeWebSocket exactly like the backend feeds it, with the overview
 * Dashboard mounted the way the overview page mounts it.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import { routeState } from "$lib/ws/route-state.svelte";
import Dashboard from "./Dashboard.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

// The card scrolls its items in a bits-ui ScrollArea, which measures its
// viewport with a ResizeObserver; jsdom has none.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

/** One dashboard group of the topic view (`{arg_name: ArgData}`). */
type GroupData = Record<string, ArgData>;
/** One card of the topic view (`{group_name: GroupData}`). */
type CardData = Record<string, GroupData>;
/** The dashboard topic view (`{card_name: CardData}`). */
type ViewData = Record<string, CardData>;

/** The card the backend groups the dashboard items of the Dashboard task under. */
const CARD = "card-Dashboard-Oil";

/**
 * A dashboard view as the Dashboard topic carries it: an amount over its limit
 * (Oil) and a plain amount (Gems), the two forms the value line has.
 */
function makeView(oil: number, gems: number, time: string): ViewData {
  return {
    [CARD]: {
      Oil: {
        _info: {
          task: "Dashboard",
          group: "Oil",
          arg: "_info",
          dt: "dashboard-total",
          dashboard: "Total",
          dashboard_color: "#7f7f7f",
          name: "Oil",
          value: "",
        },
        Time: { task: "Dashboard", group: "Oil", arg: "Time", dt: "datetime", value: time },
        Value: { task: "Dashboard", group: "Oil", arg: "Value", dt: "input-int", value: oil, ge: 0, le: 25000 },
      },
      Gems: {
        _info: {
          task: "Dashboard",
          group: "Gems",
          arg: "_info",
          dt: "dashboard-value",
          dashboard: "Amount",
          dashboard_color: "#eb8efe",
          name: "Gems",
          value: "",
        },
        Time: { task: "Dashboard", group: "Gems", arg: "Time", dt: "datetime", value: time },
        Value: { task: "Dashboard", group: "Gems", arg: "Value", dt: "input-int", value: gems, ge: 0 },
      },
    },
  };
}

/** Delivers a whole dashboard view (config switch / first snapshot). */
function deliverFull(view: ViewData) {
  FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "Dashboard", o: "full", v: view }));
}

/** Delivers one value of the running config, as a config save does. */
function deliverValue(group: string, arg: string, value: unknown) {
  FakeWebSocket.last!.serverMessage(
    JSON.stringify({ t: "Dashboard", o: "set", k: [CARD, group, arg, "value"], v: value }),
  );
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** Mounts the overview dashboard on an open connection and returns its target. */
async function mountDashboard(): Promise<HTMLElement> {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(Dashboard, { target }));
  await flushEffects();
  FakeWebSocket.last!.serverOpen();
  await flushEffects();
  return target;
}

/** The item roots of the card, in display order. */
function items(target: HTMLElement): HTMLElement[] {
  const grid = target.querySelector("div.grid");
  return grid ? ([...grid.children] as HTMLElement[]) : [];
}

/** The items that carry the flash colors. */
function flashing(target: HTMLElement): HTMLElement[] {
  return items(target).filter((item) => item.classList.contains("bg-primary"));
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
  routeState.public = false;
  // Fresh session per test (see LangSelector.test.ts): the singleton client
  // keeps its connection generation, and a stale one would make the next
  // serverOpen look like a reconnect.
  websocketClient.disconnect();
  websocketClient.connectionGeneration = 0;
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  websocketClient.disconnect();
  vi.useRealTimers();
});

describe("TestDashboardConfigSwitch", () => {
  it("does not flash the items for the view of another config", async () => {
    const target = await mountDashboard();

    deliverFull(makeView(1234, 42, "2020-01-01T00:00:00"));
    await flushEffects();
    expect(items(target)).toHaveLength(2);
    expect(items(target)[0].textContent).toContain("1234");
    expect(flashing(target)).toHaveLength(0);

    // The user switches to another config: the whole view is replaced by the
    // one of the new config. None of these values was updated under the items
    // (they are the values the other config has), so nothing may light up.
    deliverFull(makeView(8000, 900, "2020-01-02T00:00:00"));
    await flushEffects();
    expect(items(target)).toHaveLength(2);
    expect(items(target)[0].textContent).toContain("8000");
    expect(flashing(target)).toHaveLength(0);
  });

  it("flashes the item whose value is updated in the displayed config", async () => {
    const target = await mountDashboard();
    deliverFull(makeView(1234, 42, "2020-01-01T00:00:00"));
    await flushEffects();

    deliverValue("Oil", "Value", 1235);
    await flushEffects();

    expect(flashing(target)).toHaveLength(1);
    expect(flashing(target)[0].textContent).toContain("1235");
    expect(flashing(target)[0].textContent).toContain("Oil");

    // ... and the flash still ends one second after the update
    vi.advanceTimersByTime(1000);
    await flushEffects();
    expect(flashing(target)).toHaveLength(0);
  });

  it("keeps flashing for updates that arrive after the config switch", async () => {
    const target = await mountDashboard();
    deliverFull(makeView(1234, 42, "2020-01-01T00:00:00"));
    await flushEffects();

    deliverFull(makeView(8000, 900, "2020-01-02T00:00:00"));
    await flushEffects();
    expect(flashing(target)).toHaveLength(0);

    // The item took the values of the new config as its starting point, not as
    // a reason to stop reacting: the next value of that config flashes again.
    deliverValue("Oil", "Value", 8001);
    await flushEffects();
    expect(flashing(target)).toHaveLength(1);
    expect(flashing(target)[0].textContent).toContain("8001");
  });

  it("ends the flash of the item with the view it belongs to", async () => {
    const target = await mountDashboard();
    deliverFull(makeView(1234, 42, "2020-01-01T00:00:00"));
    await flushEffects();

    // An update of the displayed config lights the item up ...
    deliverValue("Oil", "Value", 1235);
    await flushEffects();
    expect(flashing(target)).toHaveLength(1);
    vi.advanceTimersByTime(400);

    // ... and the switch to another config takes the highlight with it: it is
    // about values that just left the screen, not about the ones that arrived.
    deliverFull(makeView(8000, 900, "2020-01-02T00:00:00"));
    await flushEffects();
    expect(flashing(target)).toHaveLength(0);

    // The second the update started does not resurface on the new values
    vi.advanceTimersByTime(1000);
    await flushEffects();
    expect(flashing(target)).toHaveLength(0);
  });
});
