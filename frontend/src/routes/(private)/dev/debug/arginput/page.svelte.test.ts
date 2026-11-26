/**
 * Tests for the advanced settings demo of the arg input page.
 *
 * The page shows two cards of the same content, one with a Scheduler group and
 * one without, so the cog of the card header can be checked in both header
 * shapes: an arg marked `advanced` stays out of the card until the cog is
 * opened, the rows it reveals carry a gray pill, and a card without an advanced
 * arg has no cog at all.
 *
 * The file name ends with `.svelte.test.ts`, so the svelte plugin compiles it
 * as a svelte module (runes are available) and vitest still picks it up as
 * `*.test.ts`.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import Page from "./+page.svelte";

vi.mock("svelte-sonner", () => ({
  toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

/** jsdom has no ResizeObserver; the page only needs one that exists */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/** jsdom has no IntersectionObserver either, and the card list measures with it */
class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

async function settle() {
  for (let i = 0; i < 2; i++) {
    await flushEffects();
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  await flushEffects();
}

/** Mount the demo page and return its target element. */
async function mountPage() {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(Page, { target }));
  await settle();
  return target;
}

/** One card of the list, keyed the way ArgCardList keys its cards */
function card(target: HTMLElement, cardKey: string): HTMLElement {
  const element = target.querySelector<HTMLElement>(`[data-group-key="${cardKey}"]`);
  expect(element, `no card "${cardKey}" on the page`).not.toBeNull();
  return element!;
}

/** The advanced settings toggle of a card */
function advancedToggle(element: HTMLElement): HTMLButtonElement | undefined {
  return [...element.querySelectorAll<HTMLButtonElement>("button")].find((button) =>
    button.textContent?.includes(t.Input.Advanced()),
  );
}

function advancedPills(element: HTMLElement): Element[] {
  return [...element.querySelectorAll('[data-slot="badge"]')];
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("TestAdvancedDemo", () => {
  it("shows a cog on both demo cards", async () => {
    const target = await mountPage();

    expect(advancedToggle(card(target, "AdvancedScheduler"))).not.toBeUndefined();
    expect(advancedToggle(card(target, "AdvancedPlain"))).not.toBeUndefined();
  });

  it("keeps the advanced rows of the scheduler card out of sight", async () => {
    const target = await mountPage();
    const demo = card(target, "AdvancedScheduler");

    // the plain row and the scheduler badge are always on the card
    expect(demo.textContent).toContain("select (always shown)");
    expect(demo.textContent).toContain("ENABLED");
    expect(demo.textContent).not.toContain("scheduler arg (advanced)");
    expect(demo.textContent).not.toContain("input-int (advanced)");
    expect(demo.textContent).not.toContain("input-float (advanced)");
    expect(advancedPills(demo)).toHaveLength(0);
  });

  it("reveals the scheduler arg and the group of advanced args on a click", async () => {
    const target = await mountPage();
    const demo = card(target, "AdvancedScheduler");

    advancedToggle(demo)!.click();
    await settle();

    // a scheduler arg other than Enable / NextRun is behind the same toggle
    expect(demo.textContent).toContain("scheduler arg (advanced)");
    expect(demo.textContent).toContain("input-int (advanced)");
    // Tuning holds advanced args only, so it comes with them
    expect(demo.textContent).toContain("input-float (advanced)");
    expect(advancedPills(demo)).toHaveLength(4);
  });

  it("keeps the toggle of the plain card independent", async () => {
    const target = await mountPage();
    const withScheduler = card(target, "AdvancedScheduler");
    const plain = card(target, "AdvancedPlain");

    advancedToggle(withScheduler)!.click();
    await settle();

    // the other card is still closed, and its scheduler arg does not exist
    expect(plain.textContent).toContain("select (always shown)");
    expect(plain.textContent).not.toContain("scheduler arg (advanced)");
    expect(plain.textContent).not.toContain("input-int (advanced)");
    expect(advancedPills(plain)).toHaveLength(0);

    advancedToggle(plain)!.click();
    await settle();

    expect(plain.textContent).toContain("input-int (advanced)");
    expect(plain.textContent).toContain("input-float (advanced)");
    expect(advancedPills(plain)).toHaveLength(3);
  });

  it("expands the folded help of a plain row with the toggle", async () => {
    const target = await mountPage();
    const plain = card(target, "AdvancedPlain");
    const help = "Folded help: the question mark expands it, and so does opening the advanced settings";

    expect(plain.textContent).not.toContain(help);
    advancedToggle(plain)!.click();
    await settle();

    expect(plain.textContent).toContain(help);
  });

  it("offers no cog on a card without an advanced arg", async () => {
    const target = await mountPage();

    expect(advancedToggle(card(target, "Input"))).toBeUndefined();
  });
});
