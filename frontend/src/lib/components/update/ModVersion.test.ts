/**
 * Tests for the version pill of a mod (ModVersion.svelte)
 *
 * The pill was abstracted from the title row of ModInfo: the tests pin what
 * the mod cards and the update toast both depend on -- the shortened sha1, a
 * tag as it is, the fallback of a missing version, the raw version in the
 * tooltip, and the class of the caller winning over the badge's own sizing
 * (the toast constrains the pill to half of its row).
 */
import { mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ModVersion from "./ModVersion.svelte";

let mounted: any[] = [];

// 40 hex characters: the sha1 of a packed commit, shown shortened to 8
const SHA = "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8";

async function mountPill(props: { version: string; class?: string }) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(ModVersion, { target, props }));
  await flushEffects();
  return target;
}

/** textContent with the whitespace of the template collapsed. */
function text(element: HTMLElement): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("ModVersion", () => {
  afterEach(() => {
    for (const component of mounted) void unmount(component);
    mounted = [];
    document.body.innerHTML = "";
  });

  it("shortens the sha1 of a packed commit to 8 characters", async () => {
    const target = await mountPill({ version: SHA });

    expect(text(target)).toBe(SHA.slice(0, 8));
    expect(text(target)).not.toContain(SHA);
  });

  it("renders a tag version as it is", async () => {
    const target = await mountPill({ version: "0.9.2" });

    expect(text(target)).toBe("0.9.2");
  });

  it("falls back to the unavailable message for a missing version", async () => {
    const target = await mountPill({ version: "" });

    expect(text(target)).toBe(t.Update.VersionUnavailable());
  });

  it("keeps the raw version in the tooltip", async () => {
    const target = await mountPill({ version: SHA });

    expect(target.querySelector("[title]")?.getAttribute("title")).toBe(SHA);
  });

  it("lets the class of the caller win over the sizing of the badge", async () => {
    const target = await mountPill({ version: SHA, class: "max-w-[50%] shrink" });

    const classes = (target.querySelector("[data-slot='badge']")?.className ?? "").split(/\s+/);
    expect(classes).toContain("max-w-[50%]");
    // the caller may let the pill shrink (the toast gives it half of a row):
    // its class must win over the shrink-0 of the badge itself
    expect(classes).toContain("shrink");
    expect(classes).not.toContain("shrink-0");
  });
});
