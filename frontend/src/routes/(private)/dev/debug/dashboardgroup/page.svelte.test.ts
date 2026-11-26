/**
 * Tests for the save / cancel behaviour of the dashboard group demo page.
 *
 * The page has no backend, so it answers a save from the record editor itself
 * and applies the new record to the mock card; the dialog is the feature shown
 * here, so its own feedback (the discarded edits of a cancel) runs untouched
 * while the page adds no toast of its own. A save with nothing changed does not
 * touch the cards, and a cancel of an unchanged draft stays silent.
 *
 * The file name ends with `.svelte.test.ts`, so the svelte plugin compiles it
 * as a svelte module (runes are available) and vitest still picks it up as
 * `*.test.ts`.
 */
import { mount, unmount } from "svelte";
import { toast } from "svelte-sonner";
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

function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
}

function editButton(target: HTMLElement, index = 0): HTMLButtonElement {
  const buttons = [...target.querySelectorAll<HTMLButtonElement>(`button[aria-label="${t.Input.DashboardEdit()}"]`)];
  expect(buttons.length, "the page renders the record editor of every dashboard with a record").toBeGreaterThan(0);
  return buttons[index];
}

async function openDialog(target: HTMLElement, index = 0) {
  editButton(target, index).click();
  await settle();
}

function valueInput(): HTMLInputElement {
  return dialog()!.querySelectorAll("input")[0];
}

function footerButton(label: string): HTMLButtonElement {
  const button = [...dialog()!.querySelectorAll("button")].find((el) => el.textContent?.trim() === label);
  expect(button, `no "${label}" button in the dialog footer`).not.toBeNull();
  return button as HTMLButtonElement;
}

/** Type into the value field and commit it, as a browser does on blur. */
async function typeValue(text: string) {
  const input = valueInput();
  input.value = text;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("focusout", { bubbles: true }));
  input.dispatchEvent(new Event("blur"));
  await settle();
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("TestDashboardGroupPageSave", () => {
  it("applies the saved record to the mock card", async () => {
    const target = await mountPage();
    await openDialog(target);

    await typeValue("4321");
    footerButton(t.Input.Save()).click();
    await settle();

    // The mock card carries the saved value, the dialog is closed
    expect(dialog()).toBeNull();
    expect(target.textContent).toContain("4321");
  });

  it("adds no toast of its own for a save", async () => {
    // The save toast of a config page fires on the rpc success, which a dev
    // page does not have: faking one here would hide whether the dialog works
    const target = await mountPage();
    await openDialog(target);

    await typeValue("4321");
    footerButton(t.Input.Save()).click();
    await settle();

    expect(toast.success).not.toHaveBeenCalled();
  });

  it("leaves the cards untouched when saving an unchanged record", async () => {
    const target = await mountPage();
    const before = target.textContent;
    await openDialog(target);

    footerButton(t.Input.Save()).click();
    await settle();

    expect(dialog()).toBeNull();
    expect(target.textContent).toBe(before);
  });

  it("scrolls the dialog of a group with many values", async () => {
    // The "Many values" section is the last card of the page: 30 rows do not
    // fit the dialog, which scrolls them instead of growing past the viewport
    const target = await mountPage();
    const buttons = [...target.querySelectorAll<HTMLButtonElement>(`button[aria-label="${t.Input.DashboardEdit()}"]`)];
    buttons[buttons.length - 1].click();
    await settle();

    const rows = dialog()!.querySelectorAll('[data-slot="dashboard-edit-row"]');
    expect(rows.length).toBe(30);
    const port = dialog()!.querySelector<HTMLElement>('[class*="overflow-y-auto"]');
    expect(port).not.toBeNull();
    expect(port!.contains(rows[0])).toBe(true);
    expect(port!.contains(rows[29])).toBe(true);
  });

  it("stays silent when cancelling an unchanged record", async () => {
    const target = await mountPage();
    await openDialog(target);

    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("shows the discard toast of the dialog itself on cancel", async () => {
    // The dialog is the feature under test, so its own feedback has to reach
    // the page without the page doing anything about it
    const target = await mountPage();
    await openDialog(target);

    await typeValue("4321");
    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toast.info).mock.calls[0][0]).toBe(t.Input.DashboardEditsDiscarded());
    expect(dialog()).toBeNull();
  });
});
