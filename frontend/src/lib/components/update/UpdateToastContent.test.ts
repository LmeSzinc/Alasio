/**
 * Tests for the content of the update toast (UpdateToastContent.svelte)
 *
 * The component is a svelte-sonner custom toast: sonner injects `closeToast`
 * when the Toaster renders it (production), while the tests mount the
 * component directly and pass it, except the last one which covers the
 * component rendered without it.
 *
 * The layout is always the title row plus one row per mod (the name left, the
 * ModVersion pill right), so the tests assert the rows one by one: a missing
 * row, or a stray sentence of an older shape, fails the row list.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goto } from "$app/navigation";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import UpdateToastContent from "./UpdateToastContent.svelte";
import type { UpdateToastData } from "./updateToast";

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

// 40 hex characters: the sha1 of a packed commit, shown shortened to 8
const SHA = "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8";

async function mountContent(props: { updates: UpdateToastData[]; closeToast?: () => void }) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(UpdateToastContent, { target, props }));
  await flushEffects();
  return target;
}

/** textContent with the whitespace of the template collapsed. */
function text(element: HTMLElement): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

/**
 * One entry per update row: the text of every li, whitespace collapsed. The
 * rows are asserted apart from the title and the buttons, which Svelte neither
 * separates with a space nor the tests should depend on.
 */
function rows(target: HTMLElement): string[] {
  return [...target.querySelectorAll("li")].map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim());
}

/** The button of the given label inside a scope, or null. */
function button(scope: HTMLElement, label: string): HTMLButtonElement | null {
  return [...scope.querySelectorAll("button")].find((b) => b.textContent?.trim() === label) ?? null;
}

describe("UpdateToastContent", () => {
  afterEach(() => {
    for (const component of mounted) void unmount(component);
    mounted = [];
    document.body.innerHTML = "";
  });

  beforeEach(() => {
    vi.mocked(goto).mockClear();
  });

  it("renders the title, the row of a single update and the buttons", async () => {
    const target = await mountContent({ updates: [{ mod: "alasio", latest: SHA }] });

    expect(text(target)).toContain(t.Update.ToastTitle());
    expect(text(target)).toContain(t.Update.ToastView());
    // The row carries the name and the shortened sha1; the raw sha1 never
    // reaches the text
    expect(rows(target)).toEqual([`alasio ${SHA.slice(0, 8)}`]);
    expect(button(target, t.Update.ToastView())).not.toBeNull();
    expect(target.querySelector(`button[aria-label="${t.Update.ToastClose()}"]`)).not.toBeNull();
  });

  it("renders a tag version as it is", async () => {
    const target = await mountContent({ updates: [{ mod: "wheel_mod", latest: "0.9.2" }] });

    expect(rows(target)).toEqual(["wheel_mod 0.9.2"]);
  });

  it("renders one row per update, whatever the number", async () => {
    const target = await mountContent({
      updates: [
        { mod: "alasio", latest: SHA },
        { mod: "wheel_mod", latest: "0.9.2" },
      ],
    });

    expect(rows(target)).toEqual([`alasio ${SHA.slice(0, 8)}`, "wheel_mod 0.9.2"]);
    expect(text(target)).toContain(t.Update.ToastTitle());
  });

  it("closes the toast and opens the updater page from the view button", async () => {
    const closeToast = vi.fn();
    const target = await mountContent({ updates: [{ mod: "alasio", latest: SHA }], closeToast });

    button(target, t.Update.ToastView())!.click();
    await flushEffects();

    expect(closeToast).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/dev/updater");
  });

  it("closes the toast without navigating from the close button", async () => {
    const closeToast = vi.fn();
    const target = await mountContent({ updates: [{ mod: "alasio", latest: SHA }], closeToast });

    const close = target.querySelector<HTMLButtonElement>(`button[aria-label="${t.Update.ToastClose()}"]`);
    close!.click();
    await flushEffects();

    expect(closeToast).toHaveBeenCalledTimes(1);
    expect(goto).not.toHaveBeenCalled();
  });

  it("keeps the buttons working when sonner injected no closeToast", async () => {
    // The component is mounted directly (a render without the Toaster): both
    // buttons must be a no-op instead of throwing
    const target = await mountContent({ updates: [{ mod: "alasio", latest: SHA }] });

    button(target, t.Update.ToastView())!.click();
    target.querySelector<HTMLButtonElement>(`button[aria-label="${t.Update.ToastClose()}"]`)!.click();
    await flushEffects();

    // The view button still navigates: navigating does not depend on the toast
    expect(goto).toHaveBeenCalledWith("/dev/updater");
  });
});
