/**
 * Tests for the rising-loop wrapper (AnimationUp.svelte)
 *
 * The component clips one copy of its content into a window and rises a
 * two-copy column by exactly one copy per cycle, so the animation restarts
 * without a visible seam. The geometry is pure CSS: the tests only pin the
 * structure the CSS depends on (two copies, the duplicate hidden from
 * assistive technology, the tunables reaching the custom properties).
 */
import { createRawSnippet, mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import AnimationUp from "./AnimationUp.svelte";

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** A snippet standing in for an icon: the wrapper only copies what it gets. */
function glyphSnippet() {
  return createRawSnippet(() => ({ render: () => '<span class="glyph"></span>' }));
}

/** Mounts the wrapper around the glyph snippet. */
async function mountUp(props: Record<string, unknown> = {}) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const component = mount(AnimationUp, {
    target,
    props: { children: glyphSnippet(), class: "h-3 w-3", ...props },
  });
  mounted.push(component);
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

describe("TestAnimationUp", () => {
  it("renders the content twice, the duplicate hidden from assistive technology", async () => {
    const target = await mountUp();
    expect(target.querySelectorAll(".glyph")).toHaveLength(2);

    const copies = target.querySelectorAll(".glyph");
    // the first copy is the readable one, the second one only feeds the loop
    expect(copies[0].closest('[aria-hidden="true"]')).toBeNull();
    expect(copies[1].closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it("keeps the duplicate out of the column layout", async () => {
    // `display: contents` on the wrapper of the second copy: without it the
    // wrapper would become a flex item, the column would be three boxes tall
    // and the 50% travel would no longer equal one copy
    const target = await mountUp();
    const copies = target.querySelectorAll(".glyph");
    const wrapper = copies[1].parentElement!;
    expect(wrapper.classList.contains("anim-up-copy")).toBe(true);
    expect(wrapper.getAttribute("aria-hidden")).toBe("true");
  });

  it("clips the copies into the sized window", async () => {
    const target = await mountUp();
    const window = target.firstElementChild as HTMLElement;
    expect(window.classList.contains("anim-up-window")).toBe(true);
    expect(window.classList.contains("overflow-hidden")).toBe(true);
    expect(window.classList.contains("h-3")).toBe(true);
    expect(window.classList.contains("w-3")).toBe(true);
  });

  it("passes the tunables as custom properties", async () => {
    const target = await mountUp({ duration: "2s", fade: "2px" });
    const window = target.firstElementChild as HTMLElement;
    // the CSS reads them: the animation duration and the fade of the mask
    expect(window.style.getPropertyValue("--anim-up-duration").trim()).toBe("2s");
    expect(window.style.getPropertyValue("--anim-up-fade").trim()).toBe("2px");
  });

  it("defaults to the 0.8s cycle and a 1px fade", async () => {
    const target = await mountUp();
    const window = target.firstElementChild as HTMLElement;
    expect(window.style.getPropertyValue("--anim-up-duration").trim()).toBe("0.8s");
    expect(window.style.getPropertyValue("--anim-up-fade").trim()).toBe("1px");
  });
});
