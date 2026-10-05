/**
 * Tests for the dt=filter-order input and its editor dialog
 * (FilterOrder.svelte / FilterOrderAction.svelte / FilterOrderDialog.svelte)
 *
 * The order is shown as read-only pills, the edit button of the title row opens
 * the transfer-like dialog. Every edit only touches the dialog draft: save
 * commits the order through handleEdit, a cancel discards it with an "edits
 * discarded" toast, the footer reset button is the single-arg reset through
 * handleReset and closes the dialog. The dnd gestures themselves cannot be
 * driven in jsdom (see doc/2026-10-03_filter-order.md §10), the tests cover the
 * button paths.
 *
 * The pills are cut to five lines, measured on a hidden probe of the whole order
 * (jsdom lays nothing out, the tests fake that geometry, see fakeLayout).
 *
 * The file name ends with `.svelte.test.ts`, so the svelte plugin compiles it
 * as a svelte module: the tests can use runes (`$state`) directly, the same as
 * DashboardItem.svelte.test.ts does.
 */
import { mount, unmount } from "svelte";
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Arg from "$lib/components/arg/Arg.svelte";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";

// The "edits discarded" toast is the only svelte-sonner usage of these
// components, keep the real toast library out of the jsdom tests.
vi.mock("svelte-sonner", () => ({
  toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

/** The slice of a ResizeObserverEntry the elementSize action reads */
type ProbeEntry = { contentRect: { width: number; height: number } };

/**
 * jsdom has no ResizeObserver and lays nothing out: the stub records the
 * callbacks under the observed element, so a test can deliver a probe size by
 * hand (the bits-ui layers observe their floating layers as well).
 */
class ResizeObserverStub {
  /** The callback of every live observer, by observed element */
  static callbacks = new Map<Element, (entries: ProbeEntry[]) => void>();
  callback: (entries: ProbeEntry[]) => void;
  targets: Element[] = [];

  constructor(callback: (entries: ProbeEntry[]) => void) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.targets.push(target);
    ResizeObserverStub.callbacks.set(target, this.callback);
  }

  unobserve(target: Element) {
    ResizeObserverStub.callbacks.delete(target);
  }

  disconnect() {
    for (const target of this.targets) ResizeObserverStub.callbacks.delete(target);
    this.targets = [];
  }
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
  // The app passes the topic data (deep reactive state) down to the rows, so
  // the test passes the same shape: a plain object would not notify the pills
  // when the component commits a new order
  const data = $state(arg);
  const handleEdit = vi.fn();
  const handleReset = vi.fn();
  mounted.push(mount(Arg, { target, props: { data, handleEdit, handleReset } }));
  await settle();
  return { arg: data, target, handleEdit, handleReset };
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

/** The hidden probe of the whole order, the measuring source of the cut */
function probe(target: HTMLElement): HTMLElement {
  return target.querySelector<HTMLElement>('[data-slot="filter-order-probe"]')!;
}

/**
 * jsdom lays nothing out, so the geometry the cut reads is faked here (lines of
 * `perLine` pills of 100px with a 4px gap in between, `lineWidth` for the
 * wrapping line, `markerWidth` for the marker), then the probe size is handed
 * to the elementSize action, as a width change of the card would.
 */
async function fakeLayout(target: HTMLElement, lineWidth: number, markerWidth = 40, perLine = 2) {
  const el = probe(target);
  const units = [...el.children] as HTMLElement[];
  // The pills are the children of the probe, its last child is the marker
  const marker = units.pop()!;
  Object.defineProperty(el, "clientWidth", { value: lineWidth, configurable: true });
  el.getBoundingClientRect = () => new DOMRect(0, 0, lineWidth, 0);
  units.forEach((unit, index) => {
    Object.defineProperty(unit, "offsetTop", { value: Math.floor(index / perLine) * 22, configurable: true });
    const left = (index % perLine) * 104;
    unit.getBoundingClientRect = () => new DOMRect(left, 0, 100, 20);
  });
  marker.getBoundingClientRect = () => new DOMRect(0, 0, markerWidth, 16);
  ResizeObserverStub.callbacks.get(el)?.([{ contentRect: { width: lineWidth, height: 0 } }]);
  await flushEffects();
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
  ResizeObserverStub.callbacks.clear();
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

  it("marks an invalid item on the settings page, its tooltip lives in the editor", async () => {
    const { target } = await mountArg(makeArg({ value: ["Fleet-1", "Removed"], option: ["Fleet-1", "Fleet-2"] }));

    // The pill only carries the mark, no tooltip trigger. Screen readers still
    // get the reason through the label of the icon
    expect(pills(target).querySelector(`[role="img"][aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();
    expect(pills(target).querySelector('[data-slot="tooltip-trigger"]')).toBeNull();

    await openDialog(target);
    const row = [...column("selected").querySelectorAll<HTMLElement>('[data-slot="filter-order-item"]')].find((el) =>
      el.textContent?.includes("Removed"),
    );
    expect(row).toBeDefined();
    expect(row!.querySelector('[data-slot="tooltip-trigger"]')).not.toBeNull();
  });

  it("keeps a plain vertical row unchanged, it has no action slot", async () => {
    const { target } = await mountArg(makeArg({ dt: "textarea", value: "text", layout: "vert" }));

    expect(editButtons(target)).toEqual([]);
    expect(target.querySelector('[data-slot="filter-order-pills"]')).toBeNull();
  });
});

describe("TestFilterOrderCut", () => {
  /** 14 pills, two of them fill a line of the fake layout below */
  function longArg(): ArgData {
    const value = Array.from({ length: 14 }, (_, index) => `Item-${index + 1}`);
    return makeArg({ value, option: [...value] });
  }

  it("cuts the pills at the fifth line, the marker ends it", async () => {
    const { target } = await mountArg(longArg());
    // Without a measurement (jsdom lays nothing out) every pill is shown
    expect(pillLabels(target)).toHaveLength(14);

    await fakeLayout(target, 400);

    expect(pillLabels(target)).toEqual([
      "Item-1",
      "Item-2",
      "Item-3",
      "Item-4",
      "Item-5",
      "Item-6",
      "Item-7",
      "Item-8",
      "Item-9",
      "Item-10",
    ]);
    expect(pills(target).textContent).toContain("> ...");
    // The hidden probe keeps the whole order, it is the source of the next cut
    expect(probe(target).querySelectorAll('[data-slot="badge"]')).toHaveLength(14);
  });

  it("drops pills of the last line when the marker does not fit", async () => {
    const { target } = await mountArg(longArg());

    await fakeLayout(target, 220);

    // The second slot of the fifth line (204px) leaves no room for the marker
    expect(pillLabels(target)).toEqual([
      "Item-1",
      "Item-2",
      "Item-3",
      "Item-4",
      "Item-5",
      "Item-6",
      "Item-7",
      "Item-8",
      "Item-9",
    ]);
    expect(pills(target).textContent).toContain("> ...");
  });

  it("shows every pill when the order fits into the lines", async () => {
    const { target } = await mountArg(makeArg());

    await fakeLayout(target, 400);

    expect(pillLabels(target)).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    expect(pills(target).textContent).not.toContain("> ...");
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

describe("TestFilterOrderResetDefault", () => {
  it("sits left of cancel and resets the arg through handleReset", async () => {
    const { target, arg, handleEdit, handleReset } = await mountArg(makeArg());
    await openDialog(target);
    // A pending draft edit: the reset replaces the whole arg, the draft is
    // dropped with the dialog
    rowButton("selected", t.Input.FilterOrderRemove(), "Fleet-2").click();
    await settle();

    const reset = footerButton(t.Input.FilterOrderResetDefault());
    const cancel = footerButton(t.Input.Cancel());
    expect(reset.compareDocumentPosition(cancel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    reset.click();
    await settle();

    expect(handleReset).toHaveBeenCalledTimes(1);
    expect(handleReset.mock.calls[0][0]).toMatchObject({ task: "Test", group: "OpsiFleet", arg: "Order" });
    // A reset is neither a save nor a cancel: no edit request, no discard
    // toast, and the value is not changed locally (the backend broadcast of
    // the reset carries the default)
    expect(handleEdit).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
    expect(arg.value).toEqual(["Fleet-1", "Fleet-2", "Submarine"]);
    expect(dialog()).toBeNull();
  });

  it("resets without a pending draft edit too", async () => {
    const { target, handleReset } = await mountArg(makeArg());
    await openDialog(target);

    footerButton(t.Input.FilterOrderResetDefault()).click();
    await settle();

    // The default lives in the backend, so the button is never gated by the
    // local order
    expect(handleReset).toHaveBeenCalledTimes(1);
    expect(dialog()).toBeNull();
  });
});

describe("TestFilterOrderMultiInstance", () => {
  it("keeps two filter-order rows of one page independent", async () => {
    const argA = makeArg({ arg: "OrderA", name: "First order", value: ["Fleet-1"] });
    const argB = makeArg({ arg: "OrderB", name: "Second order", value: ["Fleet-2"] });
    const target = document.createElement("div");
    document.body.appendChild(target);
    const dataA = $state(argA);
    const dataB = $state(argB);
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
