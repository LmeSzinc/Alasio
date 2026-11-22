/**
 * The demo of the golden test review page.
 *
 * The page is a thin composition of the review workspace over the mock data, so
 * these tests mount it the way the dev nav opens it: the tree of the mock mod,
 * the source of the file it opens with, and the screenshots of that file. They
 * also follow the two edits the page owns -- picking another file of the tree
 * and hiding the screenshots.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import Page from "./+page.svelte";

/**
 * jsdom has no IntersectionObserver, which the screenshots wait for before they
 * load their source. Every observed element is reported as visible at once, so
 * the images are in the document like they are for a page that is on screen.
 */
class IntersectionObserverStub {
  #callback: IntersectionObserverCallback;

  constructor(callback: IntersectionObserverCallback) {
    this.#callback = callback;
  }

  observe(target: Element) {
    this.#callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }

  unobserve() {}

  disconnect() {}
}

describe("TestGoldenTestDemo", () => {
  let target: HTMLElement;
  let component: Record<string, unknown> | undefined;

  beforeEach(async () => {
    vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);
    // jsdom lays nothing out, scrolling to the first screenshot is a no-op
    Element.prototype.scrollIntoView = vi.fn();
    target = document.createElement("div");
    document.body.appendChild(target);
    component = mount(Page, { target });
    await flushEffects();
  });

  afterEach(() => {
    if (component !== undefined) unmount(component);
    target.remove();
  });

  function buttonByText(text: string): HTMLButtonElement {
    const button = Array.from(target.querySelectorAll("button")).find((element) =>
      element.textContent?.trim().includes(text),
    );
    if (button === undefined) throw new Error(`no button "${text}"`);
    return button;
  }

  it("opens with the tree of the mock mod", () => {
    const labels = Array.from(target.querySelectorAll("button")).map((button) => button.textContent?.trim() ?? "");

    // The map folder is the one the page opens, its files are listed already
    expect(labels).toContain("map");
    expect(labels).toContain("campaign");
    expect(labels).toContain("test_map_fleet.py");
    expect(labels).toContain("test_map_fleet_preparation.py");
    expect(labels).not.toContain("test_campaign_status.py");
  });

  it("shows the source and the screenshots of the file it opens with", () => {
    const text = target.textContent ?? "";

    expect(text).toContain("map/test_map_fleet_preparation.py");
    expect(text).toContain("Screenshot tests for fleet preparation.");
    // One screenshot per @image binding, all of them the one bundled image
    const images = Array.from(target.querySelectorAll("img"));
    expect(images).toHaveLength(4);
    for (const image of images) expect(image.getAttribute("src")).toContain("screenshot.png");
  });

  it("opens a folder and selects another file of the tree", async () => {
    buttonByText("campaign").click();
    await flushEffects();

    buttonByText("test_campaign_status.py").click();
    await flushEffects();

    const text = target.textContent ?? "";
    expect(text).toContain("campaign/test_campaign_status.py");
    expect(text).toContain("Screenshot tests for the resource status bar.");
    expect(target.querySelectorAll("img")).toHaveLength(2);
  });

  it("hides the screenshots when the toggle is pressed", async () => {
    buttonByText(t.GoldenTest.ShowImages()).click();
    await flushEffects();

    expect(target.querySelectorAll("img")).toHaveLength(0);
    // The source stays, only the second column is gone
    expect(target.textContent ?? "").toContain("Screenshot tests for fleet preparation.");
  });
});
