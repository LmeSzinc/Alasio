/**
 * Tests for the flash of DashboardItem.svelte
 *
 * The item highlights for one second when one of the values it displays
 * changes; an update that lands during that window restarts it, so the
 * highlight always ends one second after the last update. Only an update of
 * the record the item already shows does that: a record that is replaced by
 * another one (a view delivered anew, e.g. switching config) is a starting
 * point. That case needs the item re-rendered with the new record, so it is
 * covered by the overview's Dashboard.svelte.test.ts, which drives the whole
 * pipeline from the websocket message to the item. `overrideFlash` pins the
 * state for a caller that wants it fixed (the debug page uses it to show the
 * highlight).
 *
 * Props of a mounted component cannot be reassigned from the outside, so
 * the tests mount the item over a `$state` object and mutate that object:
 * svelte's deep proxy makes the mutation visible to the derived value and
 * the effect that drive the flash, exactly like the websocket client
 * feeding the dashboard topic at runtime. The runes are compiled by the
 * svelte plugin, hence the `.svelte.test.ts` file name, which vitest still
 * picks up as `*.test.ts` (and the i18n scan still ignores as a test).
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import type { ArgData } from "../arg/utils.svelte";
import DashboardItem from "./DashboardItem.svelte";

// One dashboard group, of the `Total` type: the value line shows a value over
// its limit, which gives the item both a primary and a muted text.
function makeItem(value: number): Record<string, ArgData> {
  return {
    _info: {
      group: "Oil",
      arg: "_info",
      task: "Dashboard",
      dt: "dashboard-total",
      dashboard: "Total",
      dashboard_color: "#7f7f7f",
      name: "Oil",
      value: "",
    },
    Value: { task: "Dashboard", group: "Oil", arg: "Value", dt: "input-int", value, ge: 0, le: 25000 },
    Time: { task: "Dashboard", group: "Oil", arg: "Time", dt: "datetime", value: "2020-01-01T00:00:00" },
  };
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** Mounts an item over the given data and returns its root element. */
async function mountItem(data: Record<string, ArgData>, overrideFlash?: boolean) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(DashboardItem, { target, props: { data, overrideFlash } }));
  await flushEffects();
  return { root: target.firstElementChild as HTMLElement };
}

/** True when the item carries the flash colors. */
function isFlashing(root: HTMLElement): boolean {
  return root.classList.contains("bg-primary");
}

/** The muted half of the value line (`/ 25000`), which flashes with the text. */
function tail(root: HTMLElement): HTMLElement | null {
  return root.querySelector<HTMLElement>('[class*="text-[0.8em]"]');
}

/** The color dot of the item. */
function dot(root: HTMLElement): HTMLElement | null {
  return root.querySelector<HTMLElement>("div[style]");
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  // Unmount every component the test mounted, even on assertion failure: a
  // leaked component keeps its effects (and its flash timer) alive.
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  vi.useRealTimers();
});

describe("TestDashboardItemFlash", () => {
  it("does not flash for the value it is mounted with", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);

    // The first value of an item is its starting point, not a change
    expect(root.textContent).toContain("1234");
    expect(isFlashing(root)).toBe(false);
    expect(root.classList.contains("text-primary-foreground")).toBe(false);
    expect(tail(root)?.classList.contains("text-muted-foreground")).toBe(true);
    // The color transition belongs to the steady state too: a class that only
    // arrives with the flash colors would animate the fade in, never the fade
    // out, which changes no property to begin with
    expect(root.classList.contains("transition-colors")).toBe(true);
    expect(tail(root)?.classList.contains("transition-colors")).toBe(true);
  });

  it("flashes for one second when a value changes", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);

    data.Value.value = 1235;
    await flushEffects();
    expect(isFlashing(root)).toBe(true);
    expect(root.classList.contains("text-primary-foreground")).toBe(true);
    expect(tail(root)?.classList.contains("text-primary-foreground")).toBe(true);

    // ... and is back to plain one second later, not before
    vi.advanceTimersByTime(999);
    await flushEffects();
    expect(isFlashing(root)).toBe(true);

    vi.advanceTimersByTime(1);
    await flushEffects();
    expect(isFlashing(root)).toBe(false);
    expect(root.classList.contains("text-primary-foreground")).toBe(false);
    expect(tail(root)?.classList.contains("text-muted-foreground")).toBe(true);
  });

  it("keeps the value the item shows as the only trigger", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);

    // A dashboard update moves the value and the timestamp together; the
    // timestamp alone must never flash, and neither may the static metadata
    data.Time.value = "2020-01-01T00:00:01";
    await flushEffects();
    expect(isFlashing(root)).toBe(false);

    data._info.value = "changed";
    await flushEffects();
    expect(isFlashing(root)).toBe(false);
  });

  it("restarts the second when another update lands during the flash", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);

    data.Value.value = 1235;
    await flushEffects();
    vi.advanceTimersByTime(600);

    // The second update lands while the item still flashes: the first
    // deadline (1000ms after the first change) must not end the flash
    data.Value.value = 1236;
    await flushEffects();
    vi.advanceTimersByTime(600);
    await flushEffects();
    expect(isFlashing(root)).toBe(true);

    // The flash ends one second after the last update
    vi.advanceTimersByTime(400);
    await flushEffects();
    expect(isFlashing(root)).toBe(false);
  });

  it("stays lit while the values keep changing", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);

    // Updates every 400ms, i.e. always within the flash of the previous one
    for (let i = 0; i < 10; i++) {
      data.Value.value = 1235 + i;
      await flushEffects();
      vi.advanceTimersByTime(400);
      await flushEffects();
      expect(isFlashing(root)).toBe(true);
    }
  });

  it("pins the flash state of the item with overrideFlash", async () => {
    const data = $state(makeItem(1234));
    const lit = await mountItem(data, true);
    expect(isFlashing(lit.root)).toBe(true);
    expect(tail(lit.root)?.classList.contains("text-primary-foreground")).toBe(true);

    // A pinned plain item stays plain, its values do not flash it
    const plain = $state(makeItem(1234));
    const plainRoot = (await mountItem(plain, false)).root;
    plain.Value.value = 1235;
    await flushEffects();
    expect(isFlashing(plainRoot)).toBe(false);
  });

  it("gives the dot the color of the config, and the flash foreground while it flashes", async () => {
    const data = $state(makeItem(1234));
    const { root } = await mountItem(data);
    // The config color travels in a custom property the dot paints with, so a
    // color class can take over from it
    expect(dot(root)?.getAttribute("style")).toBe("--dot-color: #7f7f7f;");
    expect(dot(root)?.classList.contains("bg-(--dot-color)")).toBe(true);
    expect(dot(root)?.classList.contains("bg-primary-foreground")).toBe(false);

    data.Value.value = 1235;
    await flushEffects();
    // The flash class replaces the config color (tailwind-merge)
    expect(dot(root)?.classList.contains("bg-primary-foreground")).toBe(true);
    expect(dot(root)?.classList.contains("bg-(--dot-color)")).toBe(false);
  });
});
