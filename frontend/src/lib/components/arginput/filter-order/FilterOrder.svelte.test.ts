/**
 * Tests for the dt=filter-order input and its editor dialog
 * (FilterOrder.svelte / FilterOrderAction.svelte / FilterOrderDialog.svelte)
 *
 * The order is shown as read-only pills, the edit button of the title row opens
 * the transfer-like dialog. Every edit only touches the dialog draft: save
 * commits the order through handleEdit, a cancel discards it with an "edits
 * discarded" toast. The dnd gestures themselves cannot be driven in jsdom (see
 * doc/2026-10-03_filter-order.md §10), the tests cover the button paths.
 */
import { mount, unmount } from "svelte";
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Arg from "$lib/components/arg/Arg.svelte";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { reactive } from "$lib/test-utils/reactive.svelte";

// The "edits discarded" toast is the only svelte-sonner usage of these
// components, keep the real toast library out of the jsdom tests.
vi.mock("svelte-sonner", () => ({
  toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

// jsdom has no ResizeObserver, which the bits-ui layers may look for.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** The arg as the backend sends it (backend sets `layout: vert` itself). */
function makeArg(overrides: Partial<ArgData> = {}): ArgData {
  return {
    task: "Test",
    group: "OpsiFleet",
    arg: "Order",
    dt: "filter-order",
    value: ["Fleet-1", "Fleet-2", "Submarine"],
    name: "Sortie order",
    option: ["Fleet-1", "Fleet-2", "Fleet-3", "Fleet-4", "Submarine", "CallSubmarine"],
    option_i18n: { CallSubmarine: "Call Submarine" },
    layout: "vert",
    ...overrides,
  };
}

async function mountArg(arg: ArgData) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  // The app passes the topic data (deep reactive state) down to the rows;
  // a plain object would not notify the pills when a sibling edits the value
  const data = reactive(arg);
  const handleEdit = vi.fn();
  mounted.push(mount(Arg, { target, props: { data, handleEdit } }));
  await settle();
  return { arg: data, target, handleEdit };
}

/**
 * jsdom runs no CSS animations, but the dialog presence still settles over a
 * frame and a tick, so every open / close waits two of them.
 */
async function settle() {
  for (let i = 0; i < 2; i++) {
    await flushEffects();
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  await flushEffects();
}

function pills(target: HTMLElement): HTMLElement {
  return target.querySelector<HTMLElement>('[data-slot="filter-order-pills"]')!;
}

function pillLabels(target: HTMLElement): string[] {
  return [...pills(target).querySelectorAll<HTMLElement>('[data-slot="badge"]')].map(
    (el) => el.textContent?.trim() ?? "",
  );
}

function editButtons(target: HTMLElement): HTMLButtonElement[] {
  return [...target.querySelectorAll("button")].filter((el) =>
    el.textContent?.includes(t.Input.FilterOrderEdit()),
  ) as HTMLButtonElement[];
}

function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
}

function column(name: "selected" | "unused"): HTMLElement {
  return dialog()!.querySelector<HTMLElement>(`[data-slot="filter-order-column"][data-column="${name}"]`)!;
}

function columnLabels(name: "selected" | "unused"): string[] {
  return [...column(name).querySelectorAll<HTMLElement>('[data-slot="filter-order-label"]')].map(
    (el) => el.textContent?.trim() ?? "",
  );
}

/** The up / down / remove / add button of a row, found through its aria-label. */
function rowButton(columnName: "selected" | "unused", action: string, item: string): HTMLButtonElement {
  const button = column(columnName).querySelector<HTMLButtonElement>(`[aria-label="${action} ${item}"]`);
  expect(button, `no "${action} ${item}" button in the ${columnName} column`).not.toBeNull();
  return button!;
}

/** The footer button of the dialog with the given label. */
function footerButton(label: string): HTMLButtonElement {
  const button = [...dialog()!.querySelectorAll("button")].find((el) => el.textContent?.trim() === label);
  expect(button, `no "${label}" button in the dialog footer`).not.toBeNull();
  return button as HTMLButtonElement;
}

async function openDialog(target: HTMLElement) {
  editButtons(target)[0].click();
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

describe("TestFilterOrderRender", () => {
  it("shows the order as pills, the separator can only start a line", async () => {
    const { target } = await mountArg(makeArg());

    expect(pillLabels(target)).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    // ">" is bound to the pill after it: the first unit carries none, so a
    // wrapped line can only start with a separator, never end with one
    const units = [...pills(target).children] as HTMLElement[];
    expect(units).toHaveLength(3);
    expect(units[0].textContent?.replace(/\s+/g, "")).toBe("Fleet-1");
    expect(units[1].textContent?.replace(/\s+/g, "")).toBe(">Fleet-2");
    expect(units[2].textContent?.replace(/\s+/g, "")).toBe(">Submarine");
  });

  it("shows the option_i18n label of an item", async () => {
    const { target } = await mountArg(makeArg({ value: ["CallSubmarine", "Fleet-1"] }));

    expect(pillLabels(target)).toEqual(["Call Submarine", "Fleet-1"]);
  });

  it("shows a placeholder for an empty order", async () => {
    const { target } = await mountArg(makeArg({ value: [] }));

    expect(pillLabels(target)).toEqual([]);
    expect(pills(target).textContent).toContain(t.Input.FilterOrderEmpty());
  });

  it("renders the edit button in the title row, above the pills", async () => {
    const { target } = await mountArg(makeArg());

    const [button] = editButtons(target);
    expect(button).toBeDefined();
    expect(pills(target).contains(button)).toBe(false);
    // The title row comes before the pill row in the document order
    expect(button.compareDocumentPosition(pills(target)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps a plain vertical row unchanged, it has no action slot", async () => {
    const { target } = await mountArg(makeArg({ dt: "textarea", value: "text", layout: "vert" }));

    expect(editButtons(target)).toEqual([]);
    expect(target.querySelector('[data-slot="filter-order-pills"]')).toBeNull();
  });
});

describe("TestFilterOrderDialog", () => {
  it("opens the editor from the title row button", async () => {
    const { target } = await mountArg(makeArg());
    expect(dialog()).toBeNull();

    await openDialog(target);

    expect(dialog()).not.toBeNull();
    // The title is the arg name
    expect(dialog()!.textContent).toContain("Sortie order");
    expect(columnLabels("selected")).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    // The unused column is the rest of "option", in the option order, shown
    // with its option_i18n label
    expect(columnLabels("unused")).toEqual(["Fleet-3", "Fleet-4", "Call Submarine"]);
  });

  it("removes an item to the unused column and adds it back at the end", async () => {
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();
    expect(columnLabels("selected")).toEqual(["Fleet-1", "Submarine"]);
    expect(columnLabels("unused")).toEqual(["Fleet-2", "Fleet-3", "Fleet-4", "Call Submarine"]);

    // Adding appends at the end, it does not restore the old position
    rowButton("unused", t.Input.FilterOrderAdd(), "Fleet-2").click();
    await settle();
    expect(columnLabels("selected")).toEqual(["Fleet-1", "Submarine", "Fleet-2"]);
    expect(columnLabels("unused")).toEqual(["Fleet-3", "Fleet-4", "Call Submarine"]);
  });

  it("moves an item with the up / down buttons", async () => {
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    // The first row cannot move up, the last one cannot move down
    expect(rowButton("selected", t.Input.FilterOrderMoveUp(), "Fleet-1").disabled).toBe(true);
    expect(rowButton("selected", t.Input.FilterOrderMoveDown(), "Submarine").disabled).toBe(true);

    rowButton("selected", t.Input.FilterOrderMoveDown(), "Fleet-1").click();
    await settle();
    expect(columnLabels("selected")).toEqual(["Fleet-2", "Fleet-1", "Submarine"]);

    rowButton("selected", t.Input.FilterOrderMoveUp(), "Submarine").click();
    await settle();
    expect(columnLabels("selected")).toEqual(["Fleet-2", "Submarine", "Fleet-1"]);
  });

  it("shows the empty states of both columns", async () => {
    const { target } = await mountArg(makeArg({ value: [] }));
    await openDialog(target);

    expect(column("selected").textContent).toContain(t.Input.FilterOrderSelectedEmpty());
    rowButton("unused", t.Input.FilterOrderAdd(), "Fleet-1").click();
    await settle();
    expect(column("selected").textContent).not.toContain(t.Input.FilterOrderSelectedEmpty());
  });

  it("shows the unused empty state when everything is selected", async () => {
    const { target } = await mountArg(
      makeArg({ value: ["CallSubmarine", "Submarine"], option: ["Submarine", "CallSubmarine"] }),
    );
    await openDialog(target);

    expect(column("unused").textContent).toContain(t.Input.FilterOrderUnusedEmpty());
  });
});

describe("TestFilterOrderSaveCancel", () => {
  it("saves the changed order through handleEdit", async () => {
    const { target, arg, handleEdit } = await mountArg(makeArg());
    await openDialog(target);

    rowButton("selected", t.Input.FilterOrderRemove(), "Submarine").click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleEdit).toHaveBeenCalledTimes(1);
    expect(handleEdit.mock.calls[0][0].value).toEqual(["Fleet-1", "Fleet-2"]);
    // The value is updated optimistically and the pills show the new order
    expect(arg.value).toEqual(["Fleet-1", "Fleet-2"]);
    expect(pillLabels(target)).toEqual(["Fleet-1", "Fleet-2"]);
    expect(dialog()).toBeNull();
  });

  it("closes without a request when the order is unchanged", async () => {
    const { target, handleEdit } = await mountArg(makeArg());
    await openDialog(target);

    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleEdit).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("discards the edits and toasts on cancel", async () => {
    const { target, arg, handleEdit } = await mountArg(makeArg());
    await openDialog(target);

    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();
    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toast.info).mock.calls[0][0]).toBe(t.Input.FilterOrderReverted());
    expect(handleEdit).not.toHaveBeenCalled();
    expect(arg.value).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    expect(dialog()).toBeNull();
  });

  it("stays silent when cancelling without edits", async () => {
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("treats escape as a cancel", async () => {
    const { target, arg } = await mountArg(makeArg());
    await openDialog(target);

    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await settle();

    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(arg.value).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    expect(dialog()).toBeNull();
  });

  it("reopens with a fresh draft after a cancel", async () => {
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();
    footerButton(t.Input.Cancel()).click();
    await settle();

    // The discarded edit must not come back with the next open
    await openDialog(target);
    expect(columnLabels("selected")).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
  });
});

describe("TestFilterOrderMultiInstance", () => {
  it("keeps two filter-order rows of one page independent", async () => {
    const argA = makeArg({ arg: "OrderA", name: "First order", value: ["Fleet-1"] });
    const argB = makeArg({ arg: "OrderB", name: "Second order", value: ["Fleet-2"] });
    const target = document.createElement("div");
    document.body.appendChild(target);
    const dataA = reactive(argA);
    const dataB = reactive(argB);
    const handleEditA = vi.fn();
    const handleEditB = vi.fn();
    mounted.push(mount(Arg, { target, props: { data: dataA, handleEdit: handleEditA } }));
    mounted.push(mount(Arg, { target, props: { data: dataB, handleEdit: handleEditB } }));
    await settle();

    const buttons = editButtons(target);
    expect(buttons).toHaveLength(2);
    // Open the editor of the second row only
    buttons[1].click();
    await settle();

    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleEditB).toHaveBeenCalledTimes(1);
    expect(dataB.value).toEqual([]);
    expect(handleEditA).not.toHaveBeenCalled();
    expect(dataA.value).toEqual(["Fleet-1"]);
  });
});
