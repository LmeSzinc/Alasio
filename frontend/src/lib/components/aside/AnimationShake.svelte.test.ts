/**
 * Tests for the swaying wrapper (AnimationShake.svelte)
 *
 * The sway itself is CSS (a pendulum keyframe, disabled under
 * `prefers-reduced-motion`), which jsdom cannot animate. What the tests pin is
 * the contract the caller depends on: the content is not duplicated, it stays
 * inside the swaying box, and that box carries the class of the caller.
 */
import { createRawSnippet, mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import AnimationShake from "./AnimationShake.svelte";

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** A snippet standing in for an icon. */
function glyphSnippet() {
  return createRawSnippet(() => ({ render: () => '<span class="glyph"></span>' }));
}

/** Mounts the component around the glyph snippet. */
async function mountShake(props: Record<string, unknown> = {}) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const component = mount(AnimationShake, {
    target,
    props: { children: glyphSnippet(), ...props },
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

describe("TestAnimationShake", () => {
  it("renders the content once", async () => {
    // Unlike the rising wrapper, the sway moves the same node: a second copy
    // would sway next to the first one instead of replacing it
    const target = await mountShake();
    expect(target.querySelectorAll(".glyph")).toHaveLength(1);
  });

  it("wraps the content in the swaying box", async () => {
    const target = await mountShake();
    const wrapper = target.firstElementChild as HTMLElement;
    expect(wrapper.classList.contains("anim-shake")).toBe(true);
    expect(wrapper.querySelector(".glyph")).not.toBeNull();
  });

  it("keeps the class of the caller on the wrapper", async () => {
    const target = await mountShake({ class: "h-3 w-3" });
    const wrapper = target.firstElementChild as HTMLElement;
    expect(wrapper.classList.contains("anim-shake")).toBe(true);
    expect(wrapper.classList.contains("h-3")).toBe(true);
    expect(wrapper.classList.contains("w-3")).toBe(true);
  });
});
