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
import { dragRegionState } from "$lib/window/dragRegion.svelte";

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

/**
 * An IntersectionObserver that reports nothing on its own: a test hands the
 * intersection of one element to it, the same as a scroll would.
 */
class VisibilityObserverStub {
  /** The callback of every live observer, by observed element */
  static callbacks = new Map<Element, (entries: any[]) => void>();
  callback: (entries: any[]) => void;

  constructor(callback: (entries: any[]) => void) {
    this.callback = callback;
  }

  observe(target: Element) {
    VisibilityObserverStub.callbacks.set(target, this.callback);
  }

  unobserve(target: Element) {
    VisibilityObserverStub.callbacks.delete(target);
  }

  disconnect() {
    for (const [target, callback] of VisibilityObserverStub.callbacks) {
      if (callback === this.callback) VisibilityObserverStub.callbacks.delete(target);
    }
  }

  /** Tell the observer of the element that it is on screen */
  static intersect(target: Element) {
    VisibilityObserverStub.callbacks.get(target)?.([{ target, isIntersecting: true }]);
    VisibilityObserverStub.callbacks.delete(target);
  }
}

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

/**
 * Which pills carry the given mark, in the order of the pill line. jsdom keeps
 * the DOM order, so the flag of a pill is the flag of its index in the value.
 */
function pillMarks(target: HTMLElement, mark: string): boolean[] {
  return [...pills(target).children].map((child) => child.querySelector(mark) !== null);
}

/** The rows of the selected column, in the order of the value */
function selectedRows(): HTMLElement[] {
  return [...column("selected").querySelectorAll<HTMLElement>('[data-slot="filter-order-item"]')];
}

/** The remove button of one specific row, the label alone is not unique */
function deleteOf(row: HTMLElement, label: string): HTMLButtonElement {
  const button = row.querySelector<HTMLButtonElement>(`[aria-label="${t.Input.FilterOrderRemove()} ${label}"]`);
  expect(button, `no remove button for "${label}" in the row`).not.toBeNull();
  return button!;
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

/** The group headers of the unused column, label and selected/total count. */
function groupHeaders(): string[] {
  return [...column("unused").querySelectorAll<HTMLElement>('[data-slot="filter-order-group"]')].map(
    (el) => el.textContent?.replace(/\s+/g, " ").trim() ?? "",
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
  dragRegionState.current = null;
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

  it("draws the up / down buttons as two filled triangles", async () => {
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    const up = rowButton("selected", t.Input.FilterOrderMoveUp(), "Fleet-2");
    const down = rowButton("selected", t.Input.FilterOrderMoveDown(), "Fleet-2");

    // The halves sit in one column, the waist is the only gap between them
    expect(up.parentElement).toBe(down.parentElement);
    expect(up.parentElement!.classList.contains("flex-col")).toBe(true);
    for (const [half, tip] of [
      [up, "M6 1 11 5H1z"],
      [down, "M1 1h10L6 5z"],
    ] as const) {
      expect(half.classList.contains("h-2")).toBe(true);
      expect(half.classList.contains("w-4")).toBe(true);
      // The shape is filled with the current color, it is not an outline
      const svg = half.querySelector("svg")!;
      expect(svg.getAttribute("class")).toContain("h-1.5");
      expect(svg.getAttribute("class")).toContain("w-3");
      const path = svg.querySelector("path")!;
      expect(path.getAttribute("d")).toBe(tip);
      expect(path.getAttribute("fill")).toBe("currentColor");
      expect(path.getAttribute("stroke")).toBe("none");
    }
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

describe("TestFilterOrderPair", () => {
  it("shows a pair in the stored form, the option after the level separator", async () => {
    const { target } = await mountArg(makePairArg());

    expect(pillText(target)).toEqual(["UrgentCube-1:30", "UrgentCube-3", "Major", "shortest"]);
  });

  it("edits no secondary option in a selected row", async () => {
    // A pair is the entry itself: changing the option means adding another one
    const { target } = await mountArg(makePairArg());
    await openDialog(target);

    expect(column("selected").querySelectorAll('[data-slot="select-trigger"]')).toHaveLength(0);
  });

  it("groups the unused pairs under their item and counts the unused ones", async () => {
    const { target } = await mountArg(makePairArg({ value: ["UrgentCube-1:30", "ExtraCube-1:30"] }));
    await openDialog(target);

    // The count is what is left to pick, not what is already picked: the column
    // lists the unused entries
    expect(groupHeaders()).toEqual(["UrgentCube 2/3", "ExtraCube 1/2", `${t.Input.FilterOrderCommands()} 1/1`]);
    // The selected pair is not offered again, the bare item comes before the
    // optionCommand; a row of a group reads as the whole entry, the same as the row it
    // becomes in the selected column
    expect(columnLabels("unused")).toEqual(["UrgentCube-1:45", "UrgentCube-3", "ExtraCube-0:30", "Major", "shortest"]);
  });

  it("shows the translated name of both levels in an unused row", async () => {
    // A row is the identity of the value: the drag has to look like the row it
    // becomes, so both levels are translated and joined by the separator
    const { target } = await mountArg(
      makePairArg({
        value: [],
        option: ["RetrofitT1"],
        option_dict: { RetrofitT1: ["dd", "cl"] },
        option_command: [],
        option_i18n: { RetrofitT1: "T1 retrofit blueprint", dd: "Destroyer", cl: "Light cruiser" },
      }),
    );
    await openDialog(target);

    expect(columnLabels("unused")).toEqual(["T1 retrofit blueprint-Destroyer", "T1 retrofit blueprint-Light cruiser"]);
  });

  it("falls back to the whole entry when a level has no translation", async () => {
    // The demo of the dev page declares no option_i18n, so both levels resolve
    // to themselves and the row still reads as the whole entry
    const { target } = await mountArg(
      makePairArg({
        value: ["UrgentCube-1:30"],
        option: ["UrgentCube"],
        option_dict: { UrgentCube: ["1:30", "1:45"] },
        option_command: [],
        option_i18n: undefined,
      }),
    );
    await openDialog(target);

    expect(columnLabels("unused")).toEqual(["UrgentCube-1:45"]);
  });

  it("marks only the later occurrence of a repeated item on the settings page", async () => {
    // The first occurrence is where the item belongs, so only the row that
    // claims the position again is marked
    const { target } = await mountArg(makeArg({ value: ["Fleet-1", "Fleet-2", "Fleet-1"] }));

    const mark = `[aria-label="${t.Input.FilterOrderDuplicate()}"]`;
    expect(pillMarks(target, mark)).toEqual([false, false, true]);
  });

  it("marks only the later occurrence of a repeated row in the editor and blocks saving", async () => {
    const { target, arg } = await mountArg(makeArg({ value: ["Fleet-1", "Fleet-2", "Fleet-1"] }));
    await openDialog(target);

    expect(columnLabels("selected")).toEqual(["Fleet-1", "Fleet-2", "Fleet-1"]);
    const mark = `[aria-label="${t.Input.FilterOrderDuplicate()}"]`;
    const rows = selectedRows();
    expect(rows.map((row) => row.querySelector(mark) !== null)).toEqual([false, false, true]);
    expect(footerButton(t.Input.Save()).disabled).toBe(true);

    // Delete the FIRST row on purpose: the later occurrence takes its place as
    // the only one, so it becomes a legal row and the mark disappears
    deleteOf(rows[0], "Fleet-1").click();
    await settle();

    expect(columnLabels("selected")).toEqual(["Fleet-2", "Fleet-1"]);
    expect(column("selected").querySelector(mark)).toBeNull();
    expect(footerButton(t.Input.Save()).disabled).toBe(false);

    // The surviving row is the one that used to be the repeat, and it is saved
    // as the only occurrence of the item
    footerButton(t.Input.Save()).click();
    await settle();
    expect(arg.value).toEqual(["Fleet-2", "Fleet-1"]);
  });

  it("removes only the marked row, not the valid first occurrence", async () => {
    const { target, arg, handleEdit } = await mountArg(makeArg({ value: ["Fleet-1", "Fleet-2", "Fleet-1"] }));
    await openDialog(target);

    // Delete the third row (the repeat), the first one has to stay
    deleteOf(selectedRows()[2], "Fleet-1").click();
    await settle();

    expect(columnLabels("selected")).toEqual(["Fleet-1", "Fleet-2"]);
    footerButton(t.Input.Save()).click();
    await settle();
    expect(arg.value).toEqual(["Fleet-1", "Fleet-2"]);
    expect(handleEdit).toHaveBeenCalledTimes(1);
  });

  it("keeps the header of a group whose pairs are all selected", async () => {
    const { target } = await mountArg(makePairArg({ value: ["UrgentCube-1:30", "UrgentCube-1:45", "UrgentCube-3"] }));
    await openDialog(target);

    // The count reads 0/3 and the group offers no row any more, but the header
    // stays so the item does not vanish from the column
    expect(groupHeaders()).toEqual(["UrgentCube 0/3", "ExtraCube 2/2", `${t.Input.FilterOrderCommands()} 1/1`]);
    expect(columnLabels("unused")).toEqual(["ExtraCube-0:30", "ExtraCube-1:30", "Major", "shortest"]);
  });

  it("keeps the header of a command group whose commands are all selected", async () => {
    const { target } = await mountArg(makePairArg({ value: ["shortest"] }));
    await openDialog(target);

    expect(groupHeaders()).toEqual(["UrgentCube 3/3", "ExtraCube 2/2", `${t.Input.FilterOrderCommands()} 0/1`]);
    expect(columnLabels("unused")).toEqual([
      "UrgentCube-1:30",
      "UrgentCube-1:45",
      "UrgentCube-3",
      "ExtraCube-0:30",
      "ExtraCube-1:30",
      "Major",
    ]);
  });

  it("adds a pair of a group at the end of the order", async () => {
    const { target, arg } = await mountArg(makePairArg({ value: [] }));
    await openDialog(target);

    rowButton("unused", t.Input.FilterOrderAdd(), "UrgentCube-1:45").click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["UrgentCube-1:45"]);
  });

  it("adds a command as it is", async () => {
    const { target, arg } = await mountArg(makePairArg({ value: [] }));
    await openDialog(target);

    rowButton("unused", t.Input.FilterOrderAdd(), "shortest").click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["shortest"]);
  });

  it("keeps an unknown pair and blocks saving", async () => {
    const { target } = await mountArg(makePairArg({ value: ["UrgentCube-9:00"] }));
    await openDialog(target);

    expect(columnLabels("selected")).toEqual(["UrgentCube-9:00"]);
    expect(footerButton(t.Input.Save()).disabled).toBe(true);
  });

  it("accepts the optionCommand of the arg as legal items", async () => {
    const { target } = await mountArg(makePairArg({ value: ["shortest"] }));
    await openDialog(target);

    // A command is a legal entry, only the unknown pair blocks saving
    expect(footerButton(t.Input.Save()).disabled).toBe(false);
  });

  it("marks an entry outside the definition in both views", async () => {
    // "shortest" is not declared as a command of this arg, so it is not an item of
    // the value at all: the pill line and the editor have to agree on it
    const { target } = await mountArg(makePairArg({ value: ["UrgentCube-1:30", "shortest"], option_command: [] }));

    expect(pills(target).querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();

    await openDialog(target);
    expect(columnLabels("selected")).toEqual(["UrgentCube-1:30", "shortest"]);
    expect(column("selected").querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();
    expect(footerButton(t.Input.Save()).disabled).toBe(true);
  });

  it("marks no command on the settings page", async () => {
    // The pill line and the dialog must agree on what is legal, otherwise a
    // stored token looks broken before the editor is opened
    const { target } = await mountArg(makePairArg({ value: ["shortest"] }));

    expect(pillText(target)).toEqual(["shortest"]);
    expect(pills(target).querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).toBeNull();
  });

  it("still marks an unknown pair on the settings page", async () => {
    const { target } = await mountArg(makePairArg({ value: ["UrgentCube-9:00"] }));

    expect(pills(target).querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();
  });
});

/** The arg of dt="filter-order-item", as the backend sends it */
function makeSecondaryArg(overrides: Partial<ArgData> = {}): ArgData {
  return makeArg({
    dt: "filter-order-item",
    value: ["PlateT4-gun", "Chip"],
    option: ["PlateT4", "BookT3", "Chip"],
    option_dict: { PlateT4: ["gun", "general"], BookT3: ["red", "blue"] },
    ...overrides,
  });
}

/** The arg of dt="filter-order-pair", as the backend sends it */
function makePairArg(overrides: Partial<ArgData> = {}): ArgData {
  return makeArg({
    dt: "filter-order-pair",
    value: ["UrgentCube-1:30", "UrgentCube-3", "Major", "shortest"],
    option: ["UrgentCube", "ExtraCube", "Major"],
    option_dict: { UrgentCube: ["1:30", "1:45", "3"], ExtraCube: ["0:30", "1:30"] },
    option_command: ["shortest"],
    ...overrides,
  });
}

/** The pill labels, the gap between a primary item and its option collapsed */
function pillText(target: HTMLElement): string[] {
  return pillLabels(target).map((label) => label.replace(/\s+/g, " ").trim());
}
/** Open the secondary select of the selected column (the first row that has one) */
async function openSecondarySelect() {
  const trigger = column("selected").querySelector<HTMLElement>('[data-slot="select-trigger"]');
  expect(trigger).not.toBeNull();
  trigger!.click();
  await settle();
}

/** Pick an option of the opened select popup */
function selectOption(label: string) {
  const item = [...document.querySelectorAll<HTMLElement>('[data-slot="select-item"]')].find(
    (el) => el.textContent?.trim() === label,
  );
  expect(item, `no option "${label}" in the popup`).toBeDefined();
  // bits-ui commits an item on pointerup, a click alone does not select it
  item!.dispatchEvent(new Event("pointerdown", { bubbles: true, cancelable: true }));
  item!.dispatchEvent(new Event("pointerup", { bubbles: true }));
}

describe("TestFilterOrderSecondary", () => {
  it("shows the item in the stored form, the option after the level separator", async () => {
    const { target } = await mountArg(makeSecondaryArg());

    expect(pillText(target)).toEqual(["PlateT4-gun", "Chip"]);
  });

  it("edits the secondary option in place and saves it", async () => {
    const { target, arg, handleEdit } = await mountArg(makeSecondaryArg());
    await openDialog(target);
    expect(columnLabels("selected")).toEqual(["PlateT4", "Chip"]);
    expect(columnLabels("unused")).toEqual(["BookT3"]);

    await openSecondarySelect();
    selectOption("general");
    await settle();

    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["PlateT4-general", "Chip"]);
    expect(handleEdit).toHaveBeenCalledTimes(1);
  });

  it("adds an unused primary with its first secondary option", async () => {
    const { target, arg } = await mountArg(makeSecondaryArg({ value: ["Chip"] }));
    await openDialog(target);
    expect(columnLabels("unused")).toEqual(["PlateT4", "BookT3"]);

    rowButton("unused", t.Input.FilterOrderAdd(), "BookT3").click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["Chip", "BookT3-red"]);
  });

  it("disables saving a primary that must carry a secondary option", async () => {
    // A value written before the secondary options were declared: the backend
    // drops such an item on load, the demo shows the editor handling it
    const { target } = await mountArg(makeSecondaryArg({ value: ["PlateT4", "Chip"] }));
    await openDialog(target);

    expect(footerButton(t.Input.Save()).disabled).toBe(true);
  });

  it("marks an item that is no legal item, in its stored form", async () => {
    const { target } = await mountArg(makeSecondaryArg({ value: ["Gone-gun", "Chip"] }));

    expect(pillText(target)).toEqual(["Gone-gun", "Chip"]);
    expect(pills(target).querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();
  });

  it("marks a secondary option that is not declared and blocks saving", async () => {
    // BookT2 declares red / blue / yellow, gold was removed from the yaml
    const { target } = await mountArg(
      makeSecondaryArg({
        value: ["PlateT4-gun", "BookT2-gold"],
        option: ["PlateT4", "BookT2", "Chip"],
        option_dict: { PlateT4: ["gun", "general"], BookT2: ["red", "blue"] },
      }),
    );
    await openDialog(target);

    expect(columnLabels("selected")).toEqual(["PlateT4", "BookT2"]);
    expect(footerButton(t.Input.Save()).disabled).toBe(true);
  });

  it("gives the secondary selector 2/5 of the name + option box", async () => {
    // The share is of the box that holds the name and the option, not of the
    // whole row: the index, the grip and the order buttons are outside it, so
    // a short option like "cl" does not shrink its row
    const { target } = await mountArg(
      makeSecondaryArg({
        value: ["PlateT4-gun", "Short-cl"],
        option: ["PlateT4", "Short"],
        option_dict: { PlateT4: ["gun", "general"], Short: ["cl", "dd"] },
      }),
    );
    await openDialog(target);

    const triggers = column("selected").querySelectorAll<HTMLElement>('[data-slot="select-trigger"]');
    expect(triggers).toHaveLength(2);
    for (const trigger of triggers) {
      expect(trigger.classList.contains("w-2/5")).toBe(true);
      // the trigger and the label of the row share the same box
      const box = trigger.parentElement!;
      expect(box.querySelector('[data-slot="filter-order-label"]')).not.toBeNull();
    }
    expect(triggers[1].textContent?.trim()).toBe("cl");
  });

  it("shows a long primary item and a long secondary option in full", async () => {
    // jsdom lays nothing out, so the truncation is CSS only here: the label of
    // the item keeps both long parts, the pill is capped to the line width
    const { target } = await mountArg(
      makeSecondaryArg({
        value: ["GearDesignPlanT3-OrdnanceTestingReportT3"],
        option: ["GearDesignPlanT3"],
        option_dict: { GearDesignPlanT3: ["OrdnanceTestingReportT3", "DevelopmentMaterialT3"] },
      }),
    );

    expect(pillText(target)).toEqual(["GearDesignPlanT3-OrdnanceTestingReportT3"]);
  });

  it("centres the dialog below the drag band of the desktop client", async () => {
    // The band swallows clicks on the window top, so the dialog is centred in
    // the area below it instead of in the whole viewport
    dragRegionState.current = { height: 48, left: 0, right: 0 };
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    const style = dialog()!.getAttribute("style");
    // jsdom serializes the calc expression simplified
    expect(style).toContain("calc(50% + 24px)");
    expect(style).toContain("calc(100% - 80px)");
  });

  it("centres the dialog in the viewport without a band", async () => {
    // A plain browser has no host and therefore no band
    dragRegionState.current = null;
    const { target } = await mountArg(makeArg());
    await openDialog(target);

    const style = dialog()!.getAttribute("style");
    expect(style).toContain("calc(50% + 0px)");
    expect(style).toContain("calc(100% - 32px)");
  });
});

/** The decoder of the regex tests, in the javascript named group syntax */
const REGEX_PATTERN = "(?<series>S[1-9])?-?(?<family>DR|PRY|agir|[BCDEGHQT])?-?(?<detail>\\d{3}|\\d\\.\\d|\\d\\d?)?";

/** The arg of dt="filter-regex", the level tables are normalized by the backend */
function makeRegexArg(overrides: Partial<ArgData> = {}): ArgData {
  return makeArg({
    arg: "Filter",
    dt: "filter-regex",
    value: ["S9-DR-0.5", "S9-E-315", "reset"],
    option: undefined,
    option_i18n: { S9: "Series 9" },
    levels: [
      { name: "series", option: [{ key: {}, option: ["S8", "S9"] }] },
      {
        name: "family",
        option: [
          { key: {}, option: ["B", "C", "D", "Q"] },
          { key: { series: "S9" }, option: ["B", "C", "D", "Q", "agir"] },
        ],
      },
      {
        name: "detail",
        option: [
          { key: {}, option: ["0.5", "1", "2"] },
          { key: { family: "Q" }, option: ["0.5", "1", "2", "4"] },
          { key: { series: "S9", family: "E" }, option: ["2", "031", "315"] },
        ],
      },
    ],
    pattern: REGEX_PATTERN,
    option_command: ["reset"],
    ...overrides,
  });
}

/** The single column of the regex editor */
function regexColumn(): HTMLElement {
  return dialog()!.querySelector<HTMLElement>('[data-slot="filter-regex-column"]')!;
}

/** The rows of the regex editor, in the order of the value */
function regexRows(): HTMLElement[] {
  return [...regexColumn().querySelectorAll<HTMLElement>('[data-slot="filter-item"]')];
}

/**
 * The text of every regex row: the label of a command or of an unreadable item,
 * read from the remove button, an editable row has the selects instead.
 */
function regexRowTexts(): string[] {
  const prefix = `${t.Input.FilterOrderRemove()} `;
  return regexRows().map((row) => {
    const button = [...row.querySelectorAll<HTMLButtonElement>("button")].find((el) =>
      el.getAttribute("aria-label")?.startsWith(prefix),
    );
    return button?.getAttribute("aria-label")?.slice(prefix.length) ?? "";
  });
}

/** The constructor row at the end of the list */
function regexComposer(): HTMLElement {
  return regexColumn().querySelector<HTMLElement>('[data-slot="filter-regex-composer"]')!;
}

/** The scroll port of the regex editor, the constructor row must stay out of it */
function regexScrollList(): HTMLElement {
  return regexColumn().querySelector<HTMLElement>('[data-slot="filter-order-list"]')!;
}

/** The level select of one row, and of the constructor row */
function regexTriggers(scope: HTMLElement): HTMLElement[] {
  return [...scope.querySelectorAll<HTMLElement>('[data-slot="select-trigger"]')];
}

/** Open a level select and pick a value of its popup */
async function pickLevel(trigger: HTMLElement, label: string) {
  trigger.click();
  await settle();
  selectOption(label);
  await settle();
}

function composerButton(label: string): HTMLButtonElement {
  const button = [...regexComposer().querySelectorAll("button")].find(
    (el) => el.textContent?.trim() === label || el.getAttribute("aria-label") === label,
  );
  expect(button, `no "${label}" button in the constructor row`).not.toBeNull();
  return button as HTMLButtonElement;
}

/** The chip of one declared command, in the command list below the constructor row */
function commandChip(command: string): HTMLElement {
  const chip = [...regexComposer().querySelectorAll<HTMLElement>('[data-slot="filter-command"]')].find(
    (el) => el.querySelector('[data-slot="filter-command-label"]')?.textContent?.trim() === command,
  );
  expect(chip, `no chip for the command "${command}"`).not.toBeNull();
  return chip!;
}

/** The add button of a command chip, its aria-label carries the command */
function commandAddButton(command: string): HTMLButtonElement {
  const chip = commandChip(command);
  const button = [...chip.querySelectorAll("button")].find((el) =>
    el.getAttribute("aria-label")?.endsWith(` ${command}`),
  );
  expect(button, `no add button for the command "${command}"`).not.toBeNull();
  return button as HTMLButtonElement;
}

describe("TestFilterRegex", () => {
  it("shows the decoded levels as pills", async () => {
    const { target } = await mountArg(makeRegexArg({ value: ["S9-DR-0.5", "0.5", "reset"] }));

    expect(pillText(target)).toEqual(["Series 9-DR-0.5", "0.5", "reset"]);
  });

  it("marks only the item the pattern can not read", async () => {
    const { target } = await mountArg(makeRegexArg({ value: ["S9-E-315", "S9-X-1", "reset"] }));

    expect(pillMarks(target, `[role="img"][aria-label="${t.Input.FilterOrderInvalid()}"]`)).toEqual([
      false,
      true,
      false,
    ]);
  });

  it("opens a single column editor with a constructor row", async () => {
    const { target } = await mountArg(makeRegexArg());
    await openDialog(target);

    expect(regexRowTexts()).toEqual(["Series 9-DR-0.5", "Series 9-E-315", "reset"]);
    // A readable row is its own editor: one select per level
    expect(regexTriggers(regexRows()[0])).toHaveLength(3);
    // A command carries no level, so it is a plain label
    expect(regexTriggers(regexRows()[2])).toHaveLength(0);
    // The constructor row edits every level as well
    expect(regexTriggers(regexComposer())).toHaveLength(3);
  });

  it("heads the add area and keeps no selected heading on the single column", async () => {
    const { target } = await mountArg(makeRegexArg());
    await openDialog(target);

    // The list is one column, a "selected" heading would suggest a second one
    expect(regexColumn().textContent).not.toContain(t.Input.FilterOrderSelected());
    // The row that adds a predicate has its own heading, it is not the last row
    const add = regexColumn().querySelector<HTMLElement>('[data-slot="filter-regex-add"]')!;
    expect(add.textContent).toContain(t.Input.FilterOrderAddFilter());
    expect(add.contains(regexComposer())).toBe(true);
  });

  it("keeps the constructor row out of the scrolling list", async () => {
    // A long order must not hide the one row the user always needs
    const { target } = await mountArg(makeRegexArg());
    await openDialog(target);

    expect(regexScrollList().contains(regexComposer())).toBe(false);
    expect(regexScrollList().contains(regexRows()[0])).toBe(true);
  });

  it("fills the list on the frame after the dialog opens", async () => {
    // The click that opens the dialog must not wait for the whole list: the
    // shell is painted first, the rows follow on the next frame
    const { target } = await mountArg(makeRegexArg());
    editButtons(target)[0].click();
    await flushEffects();

    expect(dialog()).not.toBeNull();
    expect(regexRows()).toHaveLength(0);

    await settle();
    expect(regexRows()).toHaveLength(3);
  });

  it("builds the editor of a row when it comes to the screen", async () => {
    // The rows of a long filter mount as the text of their value, the selects
    // of a row are built when it is scrolled into view
    vi.stubGlobal("IntersectionObserver", VisibilityObserverStub);
    try {
      const value = Array.from({ length: 20 }, (_, index) => (index % 2 === 0 ? "Q-1" : "0.5"));
      const { target } = await mountArg(makeRegexArg({ value, option_i18n: {} }));
      await openDialog(target);

      // The first rows are prepared with the dialog
      expect(regexTriggers(regexRows()[0])).toHaveLength(3);
      // The far rows are text until they are scrolled to
      expect(regexTriggers(regexRows()[19])).toHaveLength(0);
      expect(regexRows()[19].querySelector('[data-slot="filter-regex-label"]')).not.toBeNull();

      VisibilityObserverStub.intersect(regexRows()[19]);
      await settle();

      expect(regexTriggers(regexRows()[19])).toHaveLength(3);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("frames every row with the position, the grip and the row buttons", async () => {
    // The frame of a row (index, grip, up / down, remove) is FilterItem, shared
    // by the rows; only the middle of the row is the editor of this datatype
    const { target } = await mountArg(makeRegexArg({ option_i18n: {} }));
    await openDialog(target);

    const rows = regexRows();
    expect(rows.map((row) => row.querySelector("span")!.textContent)).toEqual(["#1", "#2", "#3"]);
    expect(rows[1].querySelector(`[aria-label^="${t.Input.FilterOrderDrag()}"]`)).not.toBeNull();
    // The first row can not move up, the last one can not move down
    const up = rows[0].querySelector<HTMLButtonElement>(`[aria-label^="${t.Input.FilterOrderMoveUp()}"]`)!;
    const down = rows[2].querySelector<HTMLButtonElement>(`[aria-label^="${t.Input.FilterOrderMoveDown()}"]`)!;
    expect(up.disabled).toBe(true);
    expect(down.disabled).toBe(true);
    expect(rows[0].querySelector(`[aria-label^="${t.Input.FilterOrderRemove()}"]`)).not.toBeNull();
  });

  it("edits a level in place and saves the new item", async () => {
    const { target, arg, handleEdit } = await mountArg(makeRegexArg());
    await openDialog(target);

    // The second select of the first row is its family
    await pickLevel(regexTriggers(regexRows()[0])[1], "Q");
    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["S9-Q-0.5", "S9-E-315", "reset"]);
    expect(handleEdit).toHaveBeenCalledTimes(1);
  });

  it("adds the item described by the constructor row", async () => {
    const { target, arg } = await mountArg(makeRegexArg({ value: [], option_i18n: {} }));
    await openDialog(target);

    await pickLevel(regexTriggers(regexComposer())[0], "S9");
    await pickLevel(regexTriggers(regexComposer())[2], "1");
    expect(regexComposer().textContent).toContain("S9-1");
    composerButton(t.Input.FilterOrderAdd()).click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["S9-1"]);
  });

  it("offers every command as a chip with a grip and an add button", async () => {
    const { target } = await mountArg(makeRegexArg({ value: [] }));
    await openDialog(target);

    const chip = commandChip("reset");
    expect(chip.querySelector(`[aria-label^="${t.Input.FilterOrderDrag()}"]`)).not.toBeNull();
    expect(commandAddButton("reset").disabled).toBe(false);
    expect(chip.getAttribute("data-used")).toBe("false");
  });

  it("appends the row of a command and marks the chip as used", async () => {
    const { target, arg } = await mountArg(makeRegexArg({ value: [] }));
    await openDialog(target);

    commandAddButton("reset").click();
    await settle();

    // A command is an entry of its own: the chip stays, its button becomes the
    // check mark and is disabled, and the chip is dimmed
    expect(regexRowTexts()).toEqual(["reset"]);
    const chip = commandChip("reset");
    expect(chip.getAttribute("data-used")).toBe("true");
    const button = commandAddButton("reset");
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-label")).toBe(`${t.Input.FilterOrderAdded()} reset`);

    footerButton(t.Input.Save()).click();
    await settle();

    expect(arg.value).toEqual(["reset"]);
  });

  it("shows the command of the value as used", async () => {
    const { target } = await mountArg(makeRegexArg());
    await openDialog(target);

    expect(commandChip("reset").getAttribute("data-used")).toBe("true");
    expect(commandAddButton("reset").disabled).toBe(true);
  });

  it("bounds the command list to three lines that scroll", async () => {
    // A filter may declare a dozen commands, the add area must not push the
    // list out of the dialog
    const commands = Array.from({ length: 12 }, (_, index) => `cmd${index}`);
    const { target } = await mountArg(
      makeRegexArg({
        value: [],
        option_command: commands,
        pattern: REGEX_PATTERN,
        option_i18n: {},
      }),
    );
    await openDialog(target);

    const list = regexComposer().querySelector<HTMLElement>('[data-slot="filter-commands"]')!;
    expect(list.className).toContain("max-h-20");
    expect(list.className).toContain("overflow-y-auto");
    expect(list.querySelectorAll('[data-slot="filter-command"]')).toHaveLength(12);
  });

  it("blocks saving a repeated item", async () => {
    const { target } = await mountArg(makeRegexArg({ value: ["S9-E-315", "S9-E-315"], option_i18n: {} }));
    await openDialog(target);

    expect(footerButton(t.Input.Save()).disabled).toBe(true);
    deleteOf(regexRows()[1], "S9-E-315").click();
    await settle();

    expect(footerButton(t.Input.Save()).disabled).toBe(false);
  });

  it("blocks saving an item the pattern can not read, and removing it unblocks", async () => {
    const { target } = await mountArg(makeRegexArg({ value: ["S9-X-1", "S9-E-315"] }));
    await openDialog(target);

    expect(regexTriggers(regexRows()[0])).toHaveLength(0);
    expect(regexRows()[0].querySelector(`[aria-label="${t.Input.FilterOrderInvalid()}"]`)).not.toBeNull();
    expect(footerButton(t.Input.Save()).disabled).toBe(true);
    deleteOf(regexRows()[0], "S9-X-1").click();
    await settle();

    expect(footerButton(t.Input.Save()).disabled).toBe(false);
  });
});
