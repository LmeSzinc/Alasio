/**
 * Tests for the dt=secondary-select input (SecondarySelect.svelte)
 *
 * The popup has two columns: the group column is only a navigation aid for
 * long option lists, so clicking it just switches the preview, while clicking
 * an option commits the value through `handleEdit` (the callback the arg
 * pipeline turns into a set_config rpc).
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { flushEffects } from "$lib/test-utils/flush-effects";
import SecondarySelect from "./SecondarySelect.svelte";

/**
 * jsdom has no ResizeObserver, which the floating layer of bits-ui (the
 * popover positioning) needs to mount its content.
 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/** The arg as the backend sends it: groups plus i18n of groups and options. */
function makeArg(): ArgData {
  return {
    task: "Test",
    group: "Campaign",
    arg: "Name",
    dt: "secondary-select",
    value: "2-2",
    option_dict: {
      chapter1: ["1-1", "1-2"],
      chapter2: ["2-1", "2-2"],
      chapter3: ["3-1", "3-2"],
    },
    option_i18n: { chapter1: "Chapter 1", chapter2: "Chapter 2", chapter3: "Chapter 3" },
  };
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

async function mountInput(arg: ArgData = makeArg()) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const handleEdit = vi.fn();
  mounted.push(mount(SecondarySelect, { target, props: { data: arg, handleEdit } }));
  // The popover records its initial open state in an effect: it must run
  // before the trigger is clicked, otherwise the first open is swallowed.
  await flushEffects();
  return { arg, target, handleEdit };
}

function trigger(target: HTMLElement): HTMLButtonElement {
  return target.querySelector<HTMLButtonElement>("[data-slot='popover-trigger']")!;
}

function content(): HTMLElement | null {
  return document.querySelector<HTMLElement>("[data-slot='popover-content']");
}

function labels(selector: string): string[] {
  return [...document.querySelectorAll(selector)].map((el) => el.textContent?.trim() ?? "");
}

function activeTab(): string | undefined {
  return document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim();
}

/** The option the listbox points at with aria-activedescendant. */
function activeOption(): string | undefined {
  const id = document.querySelector('[role="listbox"]')?.getAttribute("aria-activedescendant");
  return id ? (document.getElementById(id)?.textContent?.trim() ?? undefined) : undefined;
}

/** The row carrying the ">" indicator, which marks the column the keys act on. */
function indicatorRow(): string | undefined {
  const icon = document.querySelector(
    '[role="tab"] svg.lucide-chevron-right, [role="option"] svg.lucide-chevron-right',
  );
  return icon?.closest('[role="tab"], [role="option"]')?.textContent?.trim() ?? undefined;
}

/** Sends a pointer event to an element, jsdom has no PointerEvent constructor. */
async function hover(element: Element | null) {
  element?.dispatchEvent(new Event("pointerenter"));
  await settle();
}

/** Clicks the element whose text is `text` among the elements of `selector`. */
async function clickByText(selector: string, text: string) {
  const element = [...document.querySelectorAll<HTMLElement>(selector)].find((el) => el.textContent?.trim() === text);
  expect(element, `no ${selector} with text "${text}"`).toBeDefined();
  element!.click();
  await settle();
}

/**
 * The popup renders through the presence layer and a portal, which settle
 * after more microtask turns than a single flushEffects().
 */
async function settle() {
  await flushEffects();
  await flushEffects();
}

/** Opens the popup the way a user does: a click on the trigger. */
async function openPopup(target: HTMLElement) {
  trigger(target).click();
  await settle();
}

/** Sends a key to the popup content, where the keyboard handler is bound. */
async function press(key: string) {
  content()?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  await settle();
}

beforeEach(() => {
  vi.useFakeTimers();
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
  vi.useRealTimers();
});

describe("TestSecondarySelectRender", () => {
  it("shows the label of the current option and starts closed", async () => {
    const { target } = await mountInput();

    // Only the option is shown, the group is not part of the value
    expect(trigger(target).textContent?.trim()).toBe("2-2");
    expect(content()).toBeNull();
  });

  it("falls back to the empty state without option_dict", async () => {
    const arg = makeArg();
    arg.option_dict = undefined;
    const { target } = await mountInput(arg);

    expect(trigger(target).textContent?.trim()).toBe("2-2");
    expect(content()).toBeNull();
  });
});

describe("TestSecondarySelectOpen", () => {
  it("opens on the group of the current value", async () => {
    const { target } = await mountInput();
    await openPopup(target);

    expect(labels('[role="tab"]')).toEqual(["Chapter 1", "Chapter 2", "Chapter 3"]);
    expect(activeTab()).toBe("Chapter 2");
    // Only the options of the previewed group are rendered
    expect(labels('[role="option"]')).toEqual(["2-1", "2-2"]);
    expect(document.querySelector('[role="option"][aria-selected="true"]')?.textContent?.trim()).toBe("2-2");
    // The highlight starts on the current value
    expect(activeOption()).toBe("2-2");
  });

  it("starts on the first group if the value is in no group", async () => {
    const arg = makeArg();
    arg.value = "9-9";
    const { target } = await mountInput(arg);
    await openPopup(target);

    expect(activeTab()).toBe("Chapter 1");
    expect(labels('[role="option"]')).toEqual(["1-1", "1-2"]);
  });
});

describe("TestSecondarySelectGroupColumn", () => {
  it("only switches the preview on a group click", async () => {
    const { target, handleEdit } = await mountInput();
    await openPopup(target);

    await clickByText('[role="tab"]', "Chapter 3");

    expect(activeTab()).toBe("Chapter 3");
    expect(labels('[role="option"]')).toEqual(["3-1", "3-2"]);
    // Switching the preview is not a selection
    expect(handleEdit).not.toHaveBeenCalled();
    expect(trigger(target).textContent?.trim()).toBe("2-2");
    // The popup stays open
    expect(content()).not.toBeNull();
  });
});

describe("TestSecondarySelectCommit", () => {
  it("commits the clicked option and closes the popup", async () => {
    const { target, handleEdit, arg } = await mountInput();
    await openPopup(target);
    await clickByText('[role="tab"]', "Chapter 3");

    await clickByText('[role="option"]', "3-1");

    expect(handleEdit).toHaveBeenCalledTimes(1);
    expect(handleEdit.mock.calls[0][0].value).toBe("3-1");
    // The arg data is updated optimistically, before the rpc answers
    expect(arg.value).toBe("3-1");
    expect(content()).toBeNull();
    expect(trigger(target).textContent?.trim()).toBe("3-1");
  });

  it("keeps the value when the selected option is clicked again", async () => {
    const { target, handleEdit, arg } = await mountInput();
    await openPopup(target);

    await clickByText('[role="option"]', "2-2");

    // No change, so no rpc is sent, but the popup still closes
    expect(handleEdit).not.toHaveBeenCalled();
    expect(arg.value).toBe("2-2");
    expect(content()).toBeNull();
  });
});

describe("TestSecondarySelectKeyboard", () => {
  it("moves between the columns and commits with enter", async () => {
    const { target, handleEdit } = await mountInput();
    await openPopup(target);

    // Left goes to the group column, down previews the next group
    await press("ArrowLeft");
    await press("ArrowDown");
    expect(activeTab()).toBe("Chapter 3");
    expect(labels('[role="option"]')).toEqual(["3-1", "3-2"]);
    expect(handleEdit).not.toHaveBeenCalled();

    // Right goes back to the options column, down moves the highlight
    await press("ArrowRight");
    await press("ArrowDown");
    await press("Enter");

    expect(handleEdit).toHaveBeenCalledTimes(1);
    expect(handleEdit.mock.calls[0][0].value).toBe("3-2");
    expect(content()).toBeNull();
  });

  it("does not move past the ends of a column", async () => {
    const { target, handleEdit } = await mountInput();
    await openPopup(target);

    // Opens on the current value, which is the last option of its group
    expect(activeOption()).toBe("2-2");
    await press("ArrowDown");
    expect(activeOption()).toBe("2-2");

    // The first option stays on top when moving up
    await press("ArrowUp");
    await press("ArrowUp");
    expect(activeOption()).toBe("2-1");

    await press("Enter");
    expect(handleEdit).toHaveBeenCalledTimes(1);
    expect(handleEdit.mock.calls[0][0].value).toBe("2-1");
  });

  it("closes on escape", async () => {
    const { target } = await mountInput();
    await openPopup(target);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await settle();

    expect(content()).toBeNull();
  });
});

describe("TestSecondarySelectColumnIndicator", () => {
  it("marks the column the arrow keys act on", async () => {
    const { target } = await mountInput();
    await openPopup(target);

    // The highlight starts in the options column
    expect(indicatorRow()).toBe("2-2");

    await press("ArrowLeft");
    expect(indicatorRow()).toBe("Chapter 2");

    await press("ArrowDown");
    expect(indicatorRow()).toBe("Chapter 3");

    await press("ArrowRight");
    expect(indicatorRow()).toBe("3-1");
  });

  it("moves the indicator to the hovered column", async () => {
    const { target } = await mountInput();
    await openPopup(target);

    await hover(document.querySelector('[role="tablist"]'));
    expect(indicatorRow()).toBe("Chapter 2");

    await hover(document.querySelector('[role="listbox"]'));
    expect(indicatorRow()).toBe("2-2");
  });
});
