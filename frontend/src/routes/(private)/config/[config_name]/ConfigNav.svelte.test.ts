/**
 * Tests for the navigation of a config whose mod has no navigation data.
 *
 * Leaving a config whose mod has navigation data for one whose mod has none
 * rebinds the ConfigNav topic to an empty source. The backend states that the
 * topic has no data (`o: "del"` at the data root) instead of pushing a full,
 * so the client must drop the navigation of the previous config; otherwise it
 * would keep showing the previous config's groups and cards forever (an empty
 * snapshot is not pushed, so nothing would ever replace them).
 *
 * The test drives the real pipeline: the singleton ws client `useTopic` uses,
 * fed by FakeWebSocket exactly like the backend feeds it, with ConfigNav
 * mounted the way the config layout mounts it.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sidebarRowClass, sidebarRowIndicatorClass } from "$lib/components/sidebar-accordion";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import { routeState } from "$lib/ws/route-state.svelte";
import ConfigNav from "./ConfigNav.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

// The nav scrolls in a bits-ui ScrollArea, which measures its viewport with a
// ResizeObserver; jsdom has none.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

/** Delivers the navigation of a mod: one group with one card. */
function deliverNav(group: string) {
  FakeWebSocket.last!.serverMessage(
    JSON.stringify({
      t: "ConfigNav",
      o: "full",
      v: { [group]: { _info: { i18n: group }, task1: { i18n: "Task 1" } } },
    }),
  );
}

/**
 * The empty-state placeholder of the nav, or null when the nav has data. It is
 * laid out as a row of the sidebar family (the row metrics, the space of the
 * select indicator) and carries the style of the empty log panel
 * (LogDisplay.svelte): muted, small and italic.
 */
function emptyPlaceholder(target: HTMLElement): HTMLElement | null {
  return target.querySelector<HTMLElement>("span.italic");
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** Mounts ConfigNav on an open connection and returns its target. */
async function mountNav(): Promise<HTMLElement> {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(ConfigNav, { target }));
  await flushEffects();
  FakeWebSocket.last!.serverOpen();
  await flushEffects();
  return target;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.reset();
  routeState.public = false;
  // Fresh session per test (see the overview Dashboard.svelte.test.ts): the
  // singleton client keeps its connection generation, and a stale one would
  // make the next serverOpen look like a reconnect.
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

describe("TestConfigNavEmptyConfig", () => {
  it("drops the previous config's navigation when the topic has no data", async () => {
    const target = await mountNav();

    // The config of a mod with navigation data: its groups are displayed
    deliverNav("main");
    await flushEffects();
    expect(target.textContent).toContain("main");

    // Switch to a config whose mod has no navigation data: the backend states
    // the topic has no data, the previous navigation must not stay
    FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "ConfigNav", o: "del" }));
    await flushEffects();
    expect(target.textContent).not.toContain("main");

    // ... the placeholder replaces it, in the style of the empty log panel
    const placeholder = emptyPlaceholder(target);
    expect(placeholder?.textContent).toBe("No navigation data");
    expect(placeholder?.className).toBe("text-muted-foreground text-sm italic");

    // ... laid out as a row of the sidebar family: the metrics of a row and
    // the space of the select indicator before the label, so the placeholder
    // lines up with the entries above instead of being a paragraph of its own
    const row = placeholder?.parentElement;
    expect(row?.className).toContain(sidebarRowClass);
    const indicator = row?.querySelector<HTMLElement>('div[aria-hidden="true"]');
    expect(indicator?.className).toContain(sidebarRowIndicatorClass);
  });

  it("displays the navigation of the config opened after an empty one", async () => {
    const target = await mountNav();

    deliverNav("main");
    await flushEffects();
    FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "ConfigNav", o: "del" }));
    await flushEffects();
    expect(emptyPlaceholder(target)).not.toBeNull();

    // The next config has navigation data: its groups replace the placeholder
    deliverNav("opsi");
    await flushEffects();
    expect(target.textContent).toContain("opsi");
    expect(emptyPlaceholder(target)).toBeNull();
  });
});
