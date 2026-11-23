/**
 * Tests for the height stages of the overview page.
 *
 * The landscape page stacks the preview, the progress bar and the dashboard in
 * one column, and only the preview can shrink. When the window gets short, the
 * cards are dropped in order of importance, each one handing its height to the
 * ones that remain:
 *   1. the progress bar, once the column cannot hold the preview minimum, the
 *      progress bar and the dashboard minimum plus the gaps between them, so
 *      the preview is never squeezed to nothing by a placeholder,
 *   2. the dashboard, once the column cannot hold the preview and dashboard
 *      minimums.
 * The preview then takes the whole column and stretches the frame over it.
 * The stage heights are short pages, so they use the compact spacing
 * (p-2.5 / gap-2.5) of the height budget.
 *
 * jsdom has no layout, so the page box is reported through the ResizeObserver
 * of the `elementSize` action, the same way a browser resize reports it.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import { routeState } from "$lib/ws/route-state.svelte";
import Page from "./+page.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

/**
 * jsdom has no ResizeObserver. Every observer records its targets, so the test
 * can report the page box through the observer the page itself measured with
 * (the ScrollAreas observe their viewports as well).
 */
class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];
  #callback: ResizeObserverCallback;
  targets: Element[] = [];

  constructor(callback: ResizeObserverCallback) {
    this.#callback = callback;
    ResizeObserverStub.instances.push(this);
  }

  observe(target: Element) {
    this.targets.push(target);
  }

  unobserve(target: Element) {
    this.targets = this.targets.filter((observed) => observed !== target);
  }

  disconnect() {
    this.targets = [];
  }

  /** Reports a content box of the given size to every observed element */
  resize(width: number, height: number) {
    this.#callback(
      this.targets.map((target) => ({ target, contentRect: { width, height } })) as unknown as ResizeObserverEntry[],
      this as unknown as ResizeObserver,
    );
  }

  /** The observer that watches the given element */
  static observing(target: Element): ResizeObserverStub {
    const observer = ResizeObserverStub.instances.find((instance) => instance.targets.includes(target));
    if (!observer) throw new Error("no ResizeObserver watches the page box");
    return observer;
  }
}

/** Mounted page handles, unmounted after every test (a leak keeps effects alive) */
let mounted: any[] = [];

/** Mounts the overview page with a config name, as the route mounts it */
async function mountPage(): Promise<HTMLElement> {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(Page, { target, props: { data: { success: true, config_name: "test" } } }));
  await flushEffects();
  FakeWebSocket.last?.serverOpen();
  await flushEffects();
  return target;
}

/** Reports the page box size, as a browser resize reports it */
async function resizePage(target: HTMLElement, width: number, height: number) {
  const pageBox = target.firstElementChild as HTMLElement;
  ResizeObserverStub.observing(pageBox).resize(width, height);
  await flushEffects();
}

/** The box the page padding and the gap of the page are declared on */
function pageRow(target: HTMLElement): HTMLElement {
  return (target.firstElementChild as HTMLElement).firstElementChild as HTMLElement;
}

/** The left column: the preview block stacked over the dashboard */
function leftColumn(target: HTMLElement): HTMLElement {
  const pageBox = target.firstElementChild as HTMLElement;
  const row = pageBox.firstElementChild as HTMLElement;
  return row.firstElementChild as HTMLElement;
}

/** The preview column: the preview stacked over the progress bar */
function previewColumn(target: HTMLElement): HTMLElement {
  return leftColumn(target).firstElementChild as HTMLElement;
}

/** The box the preview fills */
function previewBox(target: HTMLElement): HTMLElement {
  return previewColumn(target).firstElementChild as HTMLElement;
}

/** Whether the page holds the preview box at its minimum height (min-h-35) */
function hasPreviewMinimum(target: HTMLElement): boolean {
  return previewBox(target).classList.contains("min-h-35");
}

/** The canvas that draws the frames */
function frameCanvas(target: HTMLElement): HTMLCanvasElement {
  return previewBox(target).querySelector("canvas") as HTMLCanvasElement;
}

/** The progress bar placeholder, which has no data of its own yet */
function hasProgress(target: HTMLElement): boolean {
  return previewColumn(target).querySelector("[data-slot='progress']") !== null;
}

/** The dashboard card scrolls its items in a ScrollArea */
function hasDashboard(target: HTMLElement): boolean {
  return leftColumn(target).querySelector("[data-slot='scroll-area']") !== null;
}

beforeEach(() => {
  FakeWebSocket.reset();
  ResizeObserverStub.instances = [];
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
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
  vi.unstubAllGlobals();
});

describe("TestOverviewLandscapeStages", () => {
  it("shows the progress bar and the dashboard while the column fits both", async () => {
    const target = await mountPage();
    await resizePage(target, 1600, 900);

    expect(hasProgress(target)).toBe(true);
    expect(hasDashboard(target)).toBe(true);
    expect(previewBox(target).classList.contains("aspect-video")).toBe(true);
    expect(frameCanvas(target).classList.contains("object-contain")).toBe(true);
  });

  it("keeps the preview at its minimum height while the progress bar shows", async () => {
    // 380 (preview 140 + gap 10 + progress 80 + gap 10 + dashboard 140) + 20 (p-2.5)
    const target = await mountPage();
    await resizePage(target, 1600, 400);

    expect(hasProgress(target)).toBe(true);
    expect(hasDashboard(target)).toBe(true);
    expect(hasPreviewMinimum(target)).toBe(true);
  });

  it("hides the progress bar one pixel below that, so the preview is not squeezed", async () => {
    const target = await mountPage();
    await resizePage(target, 1600, 399);

    expect(hasProgress(target)).toBe(false);
    expect(hasDashboard(target)).toBe(true);
    // The cards that remain keep their own layout
    expect(previewBox(target).classList.contains("aspect-video")).toBe(true);
    expect(frameCanvas(target).classList.contains("object-contain")).toBe(true);
    expect(hasPreviewMinimum(target)).toBe(true);
  });

  it("keeps the dashboard while the preview and dashboard minimums still fit", async () => {
    // 290 (preview 140 + gap 10 + dashboard 140) + 20 (p-2.5)
    const target = await mountPage();
    await resizePage(target, 1600, 310);

    expect(hasProgress(target)).toBe(false);
    expect(hasDashboard(target)).toBe(true);
  });

  it("hides the dashboard below that and gives the whole column to the preview", async () => {
    const target = await mountPage();
    await resizePage(target, 1600, 309);

    expect(hasProgress(target)).toBe(false);
    expect(hasDashboard(target)).toBe(false);
    const box = previewBox(target);
    expect(box.classList.contains("flex-1")).toBe(true);
    expect(box.classList.contains("aspect-video")).toBe(false);
    // Nothing is stacked under it any more: no floor, the preview owns the column
    expect(hasPreviewMinimum(target)).toBe(false);
    // The frame ratio no longer matches the box: the frame fills it instead of letterboxing
    expect(frameCanvas(target).classList.contains("object-fill")).toBe(true);
    expect(frameCanvas(target).classList.contains("object-contain")).toBe(false);
  });

  it("keeps the portrait arrangement unchanged", async () => {
    const target = await mountPage();
    await resizePage(target, 800, 1000);

    expect(hasProgress(target)).toBe(true);
    expect(hasDashboard(target)).toBe(true);
    expect(previewBox(target).classList.contains("aspect-video")).toBe(true);
    expect(frameCanvas(target).classList.contains("object-contain")).toBe(true);
    expect(hasPreviewMinimum(target)).toBe(false);
  });
});

describe("TestOverviewSpacing", () => {
  it("uses the roomy spacing when the page has room in both directions", async () => {
    const target = await mountPage();
    await resizePage(target, 1600, 900);

    expect(pageRow(target).classList.contains("p-4")).toBe(true);
    expect(pageRow(target).classList.contains("gap-4")).toBe(true);
  });

  it("keeps the roomy spacing when the height shrinks across the landscape switch", async () => {
    const target = await mountPage();
    // 1000/900 = 1.11: portrait; 1000/800 = 1.25: landscape
    await resizePage(target, 1000, 900);
    expect(pageRow(target).classList.contains("p-4")).toBe(true);

    await resizePage(target, 1000, 800);
    expect(pageRow(target).classList.contains("p-4")).toBe(true);
  });

  it("uses the compact spacing when the page is short", async () => {
    const target = await mountPage();
    await resizePage(target, 1600, 600);

    expect(pageRow(target).classList.contains("p-2.5")).toBe(true);
    expect(pageRow(target).classList.contains("gap-2.5")).toBe(true);
  });

  it("uses the compact spacing when the page is narrow", async () => {
    const target = await mountPage();
    await resizePage(target, 600, 1000);

    expect(pageRow(target).classList.contains("p-2.5")).toBe(true);
    expect(pageRow(target).classList.contains("gap-2.5")).toBe(true);
  });
});
