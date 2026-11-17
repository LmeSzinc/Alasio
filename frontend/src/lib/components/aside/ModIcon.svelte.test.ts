/**
 * Tests for the mod icon of the configuration sidebar (ModIcon.svelte)
 *
 * The icon is the mod's own file, with a Play glyph as the fallback for a mod
 * that carries no icon. The failed load is remembered per mod: a re-render
 * that keeps the mod must not put the <img> back on screen (the browser would
 * request the same file again, and the icon would flicker while the data did
 * not change), while another mod is a different file and is tried.
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ModIcon from "./ModIcon.svelte";

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** The props of one mount, as the sidebar hands them over. */
type Props = {
  config: { mod: string };
};

/**
 * Mount the icon the way the sidebar renders it: the mod is read through a
 * getter, so a parent re-render hands over a new config object carrying the
 * same mod name. Assigning an equal string to a state property would be a
 * no-op and would never exercise the prop update that caused the flicker.
 */
async function mountIcon(mod: string) {
  const holder = $state<Props>({ config: { mod } });
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(
    mount(ModIcon, {
      target,
      props: {
        get mod() {
          return holder.config.mod;
        },
      },
    }),
  );
  await flushEffects();
  return { target, holder };
}

/** Replace the config the parent passes, keeping or changing the mod. */
async function reRender(holder: Props, mod: string) {
  holder.config = { mod };
  await flushEffects();
}

/** The icon image, or null while the fallback is shown. */
function image(target: HTMLElement) {
  return target.querySelector("img");
}

/** The fallback glyph, or null while the image is shown. */
function fallback(target: HTMLElement) {
  return target.querySelector("svg");
}

/** Fail the load of the image on screen, the way the browser reports it. */
async function failLoad(target: HTMLElement) {
  const element = image(target);
  if (element === null) throw new Error("the image is not on screen");
  element.dispatchEvent(new Event("error"));
  await flushEffects();
}

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
});

describe("TestModIcon", () => {
  it("renders the icon file of the mod", async () => {
    const { target } = await mountIcon("example_mod");

    expect(image(target)?.getAttribute("src")).toBe("/static/icon/example_mod.svg");
    expect(fallback(target)).toBeNull();
  });

  it("shows the fallback when the mod has no name", async () => {
    const { target } = await mountIcon("");

    expect(image(target)).toBeNull();
    expect(fallback(target)).not.toBeNull();
  });

  it("falls back when the image cannot be loaded", async () => {
    const { target } = await mountIcon("example_mod");

    await failLoad(target);

    expect(image(target)).toBeNull();
    expect(fallback(target)).not.toBeNull();
  });

  it("keeps the fallback when a re-render carries the same mod", async () => {
    const { target, holder } = await mountIcon("example_mod");
    await failLoad(target);
    const glyph = fallback(target);

    // a new config object carrying the same mod, which is what a topic push
    // of the config list looks like to the icon
    await reRender(holder, "example_mod");

    expect(image(target)).toBeNull();
    // the same node: the fallback was not torn down and rebuilt either
    expect(fallback(target)).toBe(glyph);
  });

  it("tries the icon of another mod", async () => {
    const { target, holder } = await mountIcon("example_mod");
    await failLoad(target);

    await reRender(holder, "alas");

    expect(image(target)?.getAttribute("src")).toBe("/static/icon/alas.svg");
    expect(fallback(target)).toBeNull();
  });

  it("reports the failure of the second mod too", async () => {
    const { target, holder } = await mountIcon("example_mod");
    await failLoad(target);
    await reRender(holder, "alas");

    await failLoad(target);

    expect(image(target)).toBeNull();
    expect(fallback(target)).not.toBeNull();
  });
});
