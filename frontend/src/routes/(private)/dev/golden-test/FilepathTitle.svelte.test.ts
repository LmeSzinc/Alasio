import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import FilepathTitle from "./FilepathTitle.svelte";

/**
 * The path title of the review page.
 *
 * It is one component so the toolbar of the workspace and the screenshot overlay
 * can not drift apart: both rows render it, and these tests pin what "the same
 * look" means -- monospace, body size, the foreground color, truncated rather
 * than wrapped, and the cell that takes the leftover width of its row.
 */
describe("TestFilepathTitle", () => {
  let target: HTMLElement;
  let component: Record<string, unknown> | undefined;

  beforeEach(() => {
    target = document.createElement("div");
    // A row like the ones that render it: the title is its flexible cell
    target.className = "flex items-center gap-2";
    document.body.appendChild(target);
  });

  afterEach(() => {
    if (component !== undefined) unmount(component);
    target.remove();
  });

  function render(props: { path: string; placeholder?: string }): HTMLElement {
    component = mount(FilepathTitle, { target, props });
    const span = target.querySelector("span");
    if (span === null) throw new Error("the title renders a span");
    return span;
  }

  it("shows the path in monospace at the body size", async () => {
    const span = render({ path: "map/test_map_fleet_preparation.py" });
    await flushEffects();

    expect(span.textContent).toBe("map/test_map_fleet_preparation.py");
    expect(span.className).toContain("font-mono");
    expect(span.className).toContain("text-sm");
    // The foreground color comes from the row, not from a muted class
    expect(span.className).not.toContain("text-muted-foreground");
  });

  it("truncates instead of wrapping and keeps the path in the tooltip", async () => {
    const span = render({ path: "map/fleet_bar/not_expanded.png" });
    await flushEffects();

    expect(span.className).toContain("truncate");
    expect(span.className).toContain("min-w-0");
    expect(span.className).toContain("flex-1");
    // Same inset as the overlay title, so the two rows line up
    expect(span.className).toContain("pl-2");
    expect(span.getAttribute("title")).toBe("map/fleet_bar/not_expanded.png");
  });

  it("shows a dash while no file is selected", async () => {
    const span = render({ path: "" });
    await flushEffects();

    expect(span.textContent?.trim()).toBe("—");
    expect(span.getAttribute("title")).toBe("");
  });

  it("takes the placeholder from the caller", async () => {
    const span = render({ path: "", placeholder: "no file" });
    await flushEffects();

    expect(span.textContent?.trim()).toBe("no file");
  });
});
