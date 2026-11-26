/**
 * Tests for the dashboard group editor (DashboardEdit.svelte and
 * DashboardEditDialog.svelte).
 *
 * A dashboard group carries an edit button right of its value. The dialog edits
 * every editable arg of the group with the arg rows of the config pages (Arg
 * picks the input and the layout of the datatype), keeps a draft, moves the time
 * to now while the record value is edited, and only save commits the changed
 * fields; a cancel with a changed draft says so with a toast, exactly like the
 * filter-order dialog. The reset button of a row restores that row to the value
 * the dialog opened with, since nothing was committed yet. A group without an
 * editable arg has no button at all.
 *
 * Props of a mounted component cannot be reassigned from the outside, so the
 * tests mount the action over a `$state` object and mutate that object, the same
 * way the topic data reaches the component at runtime.
 */
import { mount, unmount } from "svelte";
import { toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArgData } from "$lib/components/arg/utils.svelte";
import { formatToLocal, parseToUTC } from "$lib/components/arginput/dateutils.svelte";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { dragRegionState } from "$lib/window/dragRegion.svelte";
import DashboardEdit from "./DashboardEdit.svelte";

vi.mock("svelte-sonner", () => ({
  toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

/** jsdom has no ResizeObserver; the dialog only needs one that exists */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const NOW = new Date("2026-11-26T10:00:00Z");

function arg(overrides: Partial<ArgData> = {}): ArgData {
  return {
    task: "Main",
    group: "Emotion1",
    arg: "Value",
    dt: "input-int",
    value: 119,
    ge: 0,
    le: 150,
    ...overrides,
  };
}

function time(value = "2026-11-26T08:00:00Z"): ArgData {
  return arg({ arg: "Time", dt: "datetime", value, ge: undefined, le: undefined });
}

/** An Amount dashboard with a record and the settings of the Emotion group */
function makeGroup(value = 119, timeValue = "2026-11-26T08:00:00Z"): Record<string, ArgData> {
  return {
    _info: {
      task: "Main",
      group: "Emotion1",
      arg: "_info",
      dt: "dashboard-amount",
      dashboard: "Amount",
      name: "Emotion1",
      value: "",
    },
    Time: time(timeValue),
    Value: arg({ value }),
    Control: arg({ arg: "Control", dt: "select", value: "prevent_yellow_face" }),
    Recover: arg({ arg: "Recover", dt: "select", value: "not_in_dormitory" }),
    Oath: arg({ arg: "Oath", dt: "checkbox", value: false }),
  };
}

/** A SoftTotal dashboard: the Total is editable as well */
function makeSoftTotal(): Record<string, ArgData> {
  return {
    _info: {
      task: "Dashboard",
      group: "WeeklyPoints",
      arg: "_info",
      dt: "dashboard-soft-total",
      dashboard: "SoftTotal",
      name: "WeeklyPoints",
      value: "",
    },
    Time: time(),
    Value: arg({ task: "Dashboard", group: "WeeklyPoints", value: 12345, le: 25000 }),
    Total: arg({ task: "Dashboard", group: "WeeklyPoints", arg: "Total", value: 23145, le: 25000 }),
  };
}

/** A Planner dashboard: the plan numbers and its pairs are all editable */
function makePlanner(): Record<string, ArgData> {
  return {
    _info: {
      task: "Dashboard",
      group: "Research",
      arg: "_info",
      dt: "dashboard-planner",
      dashboard: "Planner",
      name: "Research",
      value: "",
    },
    Progress: arg({ task: "Dashboard", group: "Research", arg: "Progress", value: 80 }),
    Eta: arg({
      task: "Dashboard",
      group: "Research",
      arg: "Eta",
      dt: "input",
      value: "5d",
      ge: undefined,
      le: undefined,
    }),
    GreenValue: arg({ task: "Dashboard", group: "Research", arg: "GreenValue", value: 8 }),
    GreenTotal: arg({ task: "Dashboard", group: "Research", arg: "GreenTotal", value: 10 }),
    Time: arg({
      task: "Dashboard",
      group: "Research",
      arg: "Time",
      dt: "datetime",
      value: "2026-11-26T08:00:00Z",
      ge: undefined,
      le: undefined,
    }),
  };
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

async function mountGroup(data: Record<string, ArgData>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const state = $state(data);
  const handleMultiEdit = vi.fn();
  const handleEdit = vi.fn();
  mounted.push(mount(DashboardEdit, { target, props: { data: state, handleMultiEdit, handleEdit } }));
  await settle();
  return { data: state, target, handleMultiEdit, handleEdit };
}

function editButton(target: HTMLElement): HTMLButtonElement | null {
  return target.querySelector<HTMLButtonElement>(`button[aria-label="${t.Input.DashboardEdit()}"]`);
}

function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
}

function dialogTitle(): string {
  return dialog()!.querySelector<HTMLElement>('[data-slot="dialog-title"]')!.textContent!.trim();
}

/** The rows of the dialog, in the order of dashboardFields() */
function rows(): HTMLElement[] {
  return [...dialog()!.querySelectorAll<HTMLElement>('[data-slot="dashboard-edit-row"]')];
}

/** The arg of every row, in order */
function rowArgs(): string[] {
  return rows().map((row) => row.dataset.arg ?? "");
}

/** The row of one arg */
function row(argName: string): HTMLElement {
  const found = rows().find((el) => el.dataset.arg === argName);
  expect(found, `no row for "${argName}"`).not.toBeUndefined();
  return found!;
}

/** The text inputs of the dialog, in the order of the rows */
function inputs(): HTMLInputElement[] {
  return [...dialog()!.querySelectorAll<HTMLInputElement>('input:not([type="checkbox"])')];
}

function valueInput(): HTMLInputElement {
  return inputs()[0];
}

function timeInput(): HTMLInputElement {
  return inputs()[1];
}

function footerButton(label: string): HTMLButtonElement {
  const button = [...dialog()!.querySelectorAll("button")].find((el) => el.textContent?.trim() === label);
  expect(button, `no "${label}" button in the dialog footer`).not.toBeNull();
  return button as HTMLButtonElement;
}

/** The reset button of the row of one input */
function resetButton(input: HTMLInputElement): HTMLButtonElement {
  const button = input.closest(".group")?.querySelector<HTMLButtonElement>('button[aria-label="Reset value"]');
  expect(button, "no reset button in the row").not.toBeNull();
  return button!;
}

async function openDialog(target: HTMLElement) {
  editButton(target)!.click();
  await settle();
}

/**
 * Type into an arg input of the dialog and commit it, the way a browser does
 * when the field loses focus (the arg input only writes the arg on blur, or
 * after its 3s debounce).
 *
 * @param input Input of the row
 * @param text Text to type
 */
async function typeInto(input: HTMLInputElement, text: string) {
  input.value = text;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("focusout", { bubbles: true }));
  input.dispatchEvent(new Event("blur"));
  await settle();
}

async function typeValue(text: string) {
  await typeInto(valueInput(), text);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  dragRegionState.current = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("TestDashboardEditButton", () => {
  it("renders the edit button of a dashboard with an editable arg", async () => {
    const { target } = await mountGroup(makeGroup());

    expect(editButton(target)).not.toBeNull();
    expect(editButton(target)!.title).toBe(t.Input.DashboardEdit());
  });

  it("renders no button for a group without an editable arg", async () => {
    // A group of computed columns only: every row is static, and a group
    // without a record has no timestamp of its own to edit
    const group: Record<string, ArgData> = {
      _info: {
        task: "Dashboard",
        group: "DynamicTotal",
        arg: "_info",
        dt: "dashboard-dynamic-total",
        dashboard: "DynamicTotal",
        name: "DynamicTotal",
        value: "",
      },
      Time: arg({
        task: "Dashboard",
        group: "DynamicTotal",
        arg: "Time",
        dt: "datetime",
        value: "2026-11-26T08:00:00Z",
      }),
      Total: arg({ task: "Dashboard", group: "DynamicTotal", arg: "Total", dt: "static", value: 40000 }),
    };

    const { target } = await mountGroup(group);

    expect(editButton(target)).toBeNull();
  });
});

describe("TestDashboardEditDialog", () => {
  it("opens with the value and the time of the record", async () => {
    const { target } = await mountGroup(makeGroup());

    expect(dialog()).toBeNull();
    await openDialog(target);

    expect(dialog()).not.toBeNull();
    expect(dialogTitle()).toBe("Emotion1");
    expect(valueInput().value).toBe("119");
    expect(timeInput().value).toBe(formatToLocal("2026-11-26T08:00:00Z"));
  });

  it("lists every editable arg of the group, the timestamp last", async () => {
    const { target } = await mountGroup(makeGroup());
    await openDialog(target);

    expect(rowArgs()).toEqual(["Value", "Control", "Recover", "Oath", "Time"]);
    // The plan numbers of the group are edited in the rows of their own
    expect(dialog()!.textContent).toContain("Emotion1.Control");
    expect(dialog()!.textContent).toContain("Emotion1.Oath");
  });

  it("lists the Total of a SoftTotal as well", async () => {
    const { target } = await mountGroup(makeSoftTotal());
    await openDialog(target);

    expect(rowArgs()).toEqual(["Value", "Total", "Time"]);
    expect(inputs().map((input) => input.value)).toEqual(["12345", "23145", formatToLocal("2026-11-26T08:00:00Z")]);
  });

  it("lists the Progress, the Eta and the timestamp of a Planner", async () => {
    const { target } = await mountGroup(makePlanner());
    await openDialog(target);

    expect(rowArgs()).toEqual(["Progress", "Eta", "GreenValue", "GreenTotal", "Time"]);
  });

  it("edits the Progress and the Eta of a Planner in one batch", async () => {
    const { target, handleMultiEdit } = await mountGroup(makePlanner());

    await openDialog(target);
    await typeInto(inputs()[0], "55");
    await typeInto(inputs()[1], "0.5d");
    footerButton(t.Input.Save()).click();
    await settle();

    // A new progress is a record of now, the ETA is edited next to it
    expect(handleMultiEdit).toHaveBeenCalledTimes(1);
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([
      { task: "Dashboard", group: "Research", arg: "Progress", value: 55 },
      { task: "Dashboard", group: "Research", arg: "Eta", value: "0.5d" },
      { task: "Dashboard", group: "Research", arg: "Time", value: NOW.toISOString() },
    ]);
  });

  it("centres the dialog below the drag band of the desktop client", async () => {
    // The band swallows clicks on the window top, so the dialog is centred in
    // the area below it instead of in the whole viewport
    dragRegionState.current = { height: 48, left: 0, right: 0 };
    const { target } = await mountGroup(makeGroup());
    await openDialog(target);

    const style = dialog()!.getAttribute("style");
    expect(style).toContain("calc(50% + 24px)");
    expect(style).toContain("calc(100% - 80px)");
  });

  it("centres the dialog in the viewport without a band", async () => {
    // A plain browser has no host and therefore no band
    dragRegionState.current = null;
    const { target } = await mountGroup(makeGroup());
    await openDialog(target);

    const style = dialog()!.getAttribute("style");
    expect(style).toContain("calc(50% + 0px)");
    expect(style).toContain("calc(100% - 32px)");
  });

  it("titles an untranslated group by its group name", async () => {
    const group = makeGroup();
    delete (group._info as any).name;
    const { target } = await mountGroup(group);

    await openDialog(target);

    // No "Emotion1._info", the internal `_info` arg is not a display name
    expect(dialogTitle()).toBe("Emotion1");
  });

  it("keeps the field column the width it has on the page", async () => {
    const { target } = await mountGroup(makeGroup());
    await openDialog(target);

    // The dialog is wide enough that the input column of the arg rows reaches
    // its 12.5rem cap, the same as on a page: 45% of the 472px inner width is
    // over the cap, so both fields read exactly as wide as the page's own
    expect(dialog()!.classList.contains("sm:max-w-[520px]")).toBe(true);

    const columns = [...dialog()!.querySelectorAll("div")].filter((el) => el.classList.contains("w-9/20"));
    expect(columns.length).toBeGreaterThan(0);
    for (const column of columns) {
      expect(column.classList.contains("max-w-50")).toBe(true);
    }
  });

  it("moves the time to now while any field is edited, saving only on confirm", async () => {
    const { target, data, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");

    // The draft only: the card and the stored record are untouched
    expect(timeInput().value).toBe(formatToLocal(NOW.toISOString()));
    expect(data.Value.value).toBe(119);
    expect(data.Time.value).toBe("2026-11-26T08:00:00Z");
    expect(handleMultiEdit).not.toHaveBeenCalled();
  });

  it("moves the time to now for a setting of the group as well", async () => {
    const { target } = await mountGroup(makeGroup());

    await openDialog(target);
    row("Oath").querySelector<HTMLElement>('[data-slot="checkbox"]')!.click();
    await settle();

    // Every field is part of the record, so every edit is a record of now
    expect(timeInput().value).toBe(formatToLocal(NOW.toISOString()));
  });

  it("keeps the time when the value is typed back to the stored one", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("119");

    // The record is not changed, so neither is its time
    expect(timeInput().value).toBe(formatToLocal("2026-11-26T08:00:00Z"));
    footerButton(t.Input.Save()).click();
    await settle();
    expect(handleMultiEdit).not.toHaveBeenCalled();
  });

  it("keeps the time it opened with while the time itself is edited", async () => {
    const { target } = await mountGroup(makeGroup());

    await openDialog(target);
    const next = formatToLocal("2026-11-26T09:00:00Z");
    await typeInto(timeInput(), next);

    // The time row is the one edit that does not move the time
    expect(timeInput().value).toBe(next);
  });

  it("moves the time to now when a value is edited after a hand-set time", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeInto(timeInput(), formatToLocal("2026-11-26T09:00:00Z"));
    await typeValue("100");
    footerButton(t.Input.Save()).click();
    await settle();

    // The value edit is the later edit of the record, so it owns the time
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([
      { task: "Main", group: "Emotion1", arg: "Value", value: 100 },
      { task: "Main", group: "Emotion1", arg: "Time", value: NOW.toISOString() },
    ]);
  });

  it("saves the changed fields as one batch", async () => {
    const { target, handleMultiEdit, handleEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleMultiEdit).toHaveBeenCalledTimes(1);
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([
      { task: "Main", group: "Emotion1", arg: "Value", value: 100 },
      { task: "Main", group: "Emotion1", arg: "Time", value: NOW.toISOString() },
    ]);
    // The batch handler wins, the per-arg handler is not used
    expect(handleEdit).not.toHaveBeenCalled();
    // A save is not a cancel, even though it closes the dialog
    expect(toast.info).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("saves a change of the time alone", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    const next = formatToLocal("2026-11-26T09:00:00Z");
    await typeInto(timeInput(), next);
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleMultiEdit).toHaveBeenCalledTimes(1);
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([
      { task: "Main", group: "Emotion1", arg: "Time", value: parseToUTC(next) },
    ]);
  });

  it("saves a change of a checkbox of the group with the record time", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    row("Oath").querySelector<HTMLElement>('[data-slot="checkbox"]')!.click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleMultiEdit).toHaveBeenCalledTimes(1);
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([
      { task: "Main", group: "Emotion1", arg: "Oath", value: true },
      { task: "Main", group: "Emotion1", arg: "Time", value: NOW.toISOString() },
    ]);
  });

  it("closes without a request when nothing changed", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleMultiEdit).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("blocks saving an out of range value", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("151");

    expect(dialog()!.textContent).toContain(t.Input.LessThanOrEqual({ value: 150 }));
    const save = footerButton(t.Input.Save());
    expect(save.disabled).toBe(true);
    save.click();
    await settle();

    expect(handleMultiEdit).not.toHaveBeenCalled();
    expect(dialog()).not.toBeNull();
  });

  it("discards the draft and toasts on cancel", async () => {
    const { target, data, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toast.info).mock.calls[0][0]).toBe(t.Input.DashboardEditsDiscarded());
    expect(handleMultiEdit).not.toHaveBeenCalled();
    expect(data.Value.value).toBe(119);
    expect(data.Time.value).toBe("2026-11-26T08:00:00Z");
    expect(dialog()).toBeNull();
  });

  it("stays silent when cancelling without edits", async () => {
    const { target } = await mountGroup(makeGroup());

    await openDialog(target);
    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });

  it("stays silent when cancelling text the draft never took", async () => {
    const { target, data } = await mountGroup(makeGroup());

    await openDialog(target);
    // The arg input refuses an out of range value, so nothing was changed and
    // there is nothing to discard
    await typeValue("151");
    footerButton(t.Input.Cancel()).click();
    await settle();

    expect(toast.info).not.toHaveBeenCalled();
    expect(data.Value.value).toBe(119);
    expect(dialog()).toBeNull();
  });

  it("reopens with a fresh draft after a cancel", async () => {
    const { target } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    footerButton(t.Input.Cancel()).click();
    await settle();

    await openDialog(target);
    expect(valueInput().value).toBe("119");
    expect(timeInput().value).toBe(formatToLocal("2026-11-26T08:00:00Z"));
  });
});

describe("TestDashboardEditReset", () => {
  it("restores the value of the row as the dialog opened", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    expect(valueInput().value).toBe("100");

    resetButton(valueInput()).click();
    await settle();

    // Back to the recorded value, not to a backend default. The time follows
    // the value, so the record is unchanged again and nothing is saved.
    expect(valueInput().value).toBe("119");
    expect(timeInput().value).toBe(formatToLocal("2026-11-26T08:00:00Z"));
    footerButton(t.Input.Save()).click();
    await settle();
    expect(handleMultiEdit).not.toHaveBeenCalled();
  });

  it("restores the time of the row, so a reset record is unchanged", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    expect(timeInput().value).toBe(formatToLocal(NOW.toISOString()));

    resetButton(valueInput()).click();
    await settle();
    resetButton(timeInput()).click();
    await settle();

    expect(timeInput().value).toBe(formatToLocal("2026-11-26T08:00:00Z"));
    footerButton(t.Input.Save()).click();
    await settle();
    expect(handleMultiEdit).not.toHaveBeenCalled();
  });

  it("keeps the other rows of the draft", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("100");
    resetButton(timeInput()).click();
    await settle();
    footerButton(t.Input.Save()).click();
    await settle();

    // Only the value was left changed, the reset of the time stays
    expect(handleMultiEdit).toHaveBeenCalledTimes(1);
    expect(handleMultiEdit.mock.calls[0][0]).toEqual([{ task: "Main", group: "Emotion1", arg: "Value", value: 100 }]);
  });

  it("clears the error of a rejected value, so the record can be saved again", async () => {
    const { target, handleMultiEdit } = await mountGroup(makeGroup());

    await openDialog(target);
    await typeValue("151");
    expect(footerButton(t.Input.Save()).disabled).toBe(true);

    resetButton(valueInput()).click();
    await settle();

    expect(valueInput().value).toBe("119");
    expect(dialog()!.textContent).not.toContain(t.Input.LessThanOrEqual({ value: 150 }));
    expect(footerButton(t.Input.Save()).disabled).toBe(false);

    // Nothing changed any more, so the save is a plain close
    footerButton(t.Input.Save()).click();
    await settle();
    expect(handleMultiEdit).not.toHaveBeenCalled();
    expect(dialog()).toBeNull();
  });
});

describe("TestDashboardEditFallback", () => {
  it("edits the changed args one by one when the caller has no batch handler", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const data = $state(makeGroup());
    const handleEdit = vi.fn();
    mounted.push(mount(DashboardEdit, { target, props: { data, handleEdit } }));
    await settle();

    await openDialog(target);
    await typeValue("100");
    footerButton(t.Input.Save()).click();
    await settle();

    expect(handleEdit).toHaveBeenCalledTimes(2);
    expect(handleEdit.mock.calls[0][0]).toMatchObject({ task: "Main", group: "Emotion1", arg: "Value", value: 100 });
    expect(handleEdit.mock.calls[1][0]).toMatchObject({
      task: "Main",
      group: "Emotion1",
      arg: "Time",
      value: NOW.toISOString(),
    });
  });
});
