/**
 * Tests for the dashboard groups of ArgCard.svelte.
 *
 * A dashboard group is one summary row: the value line of its datatype, the pair
 * summary of a Planner below it and the edit button right of the value. Every
 * editable arg of the group (the Value and Total of a record, the pairs of a
 * Planner, the settings of the group) lives in the dialog of that button, not in
 * extra rows of the card.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "$lib/i18n";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ArgCard from "./ArgCard.svelte";
import type { ArgData, CardData } from "./utils.svelte";

// The card dialog renders arg inputs, and bits-ui observes its floating layers
vi.mock("svelte-sonner", () => ({
  toast: { info: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

/** jsdom has no ResizeObserver; the dialog only needs one that exists */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function arg(group: string, argName: string, dt: string, value: any, extra: Partial<ArgData> = {}): ArgData {
  return { task: "Dashboard", group, arg: argName, dt, value, ...extra };
}

function makeCard(valueArg = "GreenValue", totalArg = "GreenTotal", pair = true): CardData {
  const pairArgs: Record<string, ArgData> = pair
    ? {
        [valueArg]: arg("Planner", valueArg, "input-int", 8, { ge: 0 }),
        [totalArg]: arg("Planner", totalArg, "input-int", 10, { ge: 0 }),
      }
    : {};
  return {
    _info: { group: "Card", arg: "_info", card: "card-Dashboard-Research", name: "Card" },
    Planner: {
      _info: {
        task: "Dashboard",
        group: "Planner",
        arg: "_info",
        dt: "dashboard-planner",
        dashboard: "Planner",
        name: "Planner",
        value: "",
      },
      Progress: arg("Planner", "Progress", "input-int", 80),
      Eta: arg("Planner", "Eta", "input", "5d"),
      ...pairArgs,
      Custom: arg("Planner", "Custom", "input", "hidden"),
      Time: arg("Planner", "Time", "datetime", "2026-10-26T00:00:00Z"),
    },
  } as unknown as CardData;
}

function makeAmountCard(): CardData {
  return {
    _info: { group: "Card", arg: "_info", card: "card-Main-Emotion", name: "Emotion" },
    Emotion1: {
      _info: {
        task: "Main",
        group: "Emotion1",
        arg: "_info",
        dt: "dashboard-amount",
        dashboard: "Amount",
        name: "Emotion1",
        value: "",
      },
      Time: arg("Emotion1", "Time", "datetime", "2026-11-26T08:00:00Z"),
      Value: arg("Emotion1", "Value", "input-int", 119, { ge: 0, le: 150 }),
      Control: arg("Emotion1", "Control", "select", "prevent_yellow_face", {
        option: ["keep_exp_bonus", "prevent_green_face", "prevent_yellow_face", "prevent_red_face"],
      }),
      Oath: arg("Emotion1", "Oath", "checkbox", false),
    },
  } as unknown as CardData;
}

let mounted: any[] = [];

async function settle() {
  for (let i = 0; i < 2; i++) {
    await flushEffects();
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  await flushEffects();
}

async function mountCard(card: CardData, handleMultiEdit?: (events: any[]) => void) {
  const data = $state(card);
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(
    mount(ArgCard, {
      target,
      props: { cardData: data, parentWidth: 800, handleEdit: () => {}, handleReset: () => {}, handleMultiEdit },
    }),
  );
  await settle();
  return target;
}

function editButton(target: HTMLElement): HTMLButtonElement | null {
  return target.querySelector<HTMLButtonElement>(`button[aria-label="${t.Input.DashboardEdit()}"]`);
}

function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
}

/** The arg of every row of the opened dialog, in order */
function dialogArgs(): string[] {
  return [...dialog()!.querySelectorAll<HTMLElement>('[data-slot="dashboard-edit-row"]')].map(
    (row) => row.dataset.arg ?? "",
  );
}

async function openDialog(target: HTMLElement) {
  editButton(target)!.click();
  await settle();
}

beforeEach(() => {
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

describe("TestArgCardTitle", () => {
  it("renders a title of one line", async () => {
    const target = await mountCard(makeCard());
    const title = target.querySelector('[data-slot="card-title"]')!;

    expect([...title.querySelectorAll("p")].map((el) => el.textContent)).toEqual(["Card"]);
  });

  it("renders a name of multiple lines as one row per line", async () => {
    const card = makeCard();
    card._info.name = ["The card", "of the task"];
    const target = await mountCard(card);
    const title = target.querySelector('[data-slot="card-title"]')!;

    expect([...title.querySelectorAll("p")].map((el) => el.textContent)).toEqual(["The card", "of the task"]);
    expect(target.textContent).not.toContain("The card,of the task");
  });
});

describe("TestArgCardPlannerPair", () => {
  it("renders a prefixed Value/Total pair under the progress line", async () => {
    const target = await mountCard(makeCard());

    expect(target.textContent).toContain("80.00%");
    expect(target.textContent).toContain(">5d");
    expect(target.textContent).toContain("8 / 10");
  });

  it("shows no pair line when no pair is defined", async () => {
    const target = await mountCard(makeCard("", "", false));

    expect(target.textContent).toContain("80.00%");
    expect(target.textContent).toContain(">5d");
    expect(target.textContent).not.toContain("8 / 10");
  });
});

describe("TestArgCardDashboardEdit", () => {
  it("offers the record editor of a dashboard group", async () => {
    const target = await mountCard(makeAmountCard());

    expect(editButton(target)).not.toBeNull();
  });

  it("puts the edit button right of the value, not in the title row", async () => {
    const target = await mountCard(makeAmountCard());
    const button = editButton(target)!;

    // The button shares the box of the value (the input side of the compact
    // dashboard row), so it reads as an action on the number
    const box = button.parentElement!;
    expect(box.textContent).toContain("119");
    expect(box.textContent).not.toContain("Emotion1");
    const value = box.firstElementChild!;
    expect(value.textContent).toContain("119");
    expect(value.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps every editable arg of the group out of the card", async () => {
    // The card is one summary row: a row per arg would duplicate the dialog,
    // and the dialog is the only place an edit is saved from
    const target = await mountCard(makeAmountCard());

    expect(target.textContent).toContain("119");
    expect(target.textContent).not.toContain("Emotion1.Control");
    expect(target.textContent).not.toContain("Emotion1.Oath");
    expect(target.querySelector('[data-slot="checkbox"]')).toBeNull();
    // Value and Time are not rows of the card either
    expect(target.querySelector('input[value="119"]')).toBeNull();
  });

  it("edits every editable arg of the group in its dialog", async () => {
    const target = await mountCard(makeAmountCard());
    await openDialog(target);

    expect(dialogArgs()).toEqual(["Value", "Control", "Oath", "Time"]);
  });

  it("offers the editor of a Planner, whose plan numbers are editable too", async () => {
    const target = await mountCard(makeCard());
    await openDialog(target);

    expect(dialogArgs()).toEqual(["Progress", "Eta", "GreenValue", "GreenTotal", "Custom", "Time"]);
  });

  it("offers no editor when every row of the group is read-only", async () => {
    // A group of computed columns only: no record arg, and no editable row
    const card = {
      _info: { group: "Card", arg: "_info", card: "card-Dashboard-DynamicTotal", name: "Card" },
      DynamicTotal: {
        _info: {
          task: "Dashboard",
          group: "DynamicTotal",
          arg: "_info",
          dt: "dashboard-dynamic-total",
          dashboard: "DynamicTotal",
          name: "DynamicTotal",
          value: "",
        },
        Total: arg("DynamicTotal", "Total", "static", 40000),
        Time: arg("DynamicTotal", "Time", "datetime", "2026-11-26T08:00:00Z"),
      },
    } as unknown as CardData;

    const target = await mountCard(card);

    expect(editButton(target)).toBeNull();
  });
});
