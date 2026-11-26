/**
 * Tests for the dashboard groups of ArgCard.svelte.
 *
 * A dashboard group is one summary row: the value line of its datatype, the pair
 * summary of a Planner below it and the edit button right of the value. Every
 * editable arg of the group (the Value and Total of a record, the pairs of a
 * Planner, the settings of the group) lives in the dialog of that button, not in
 * extra rows of the card.
 *
 * A normal group keeps showing its rows in the card, where an arg marked as an
 * advanced setting stays hidden until the toggle of the card header is opened.
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

/**
 * A card of the plain settings page: a scheduler, one group of ordinary rows
 * and one group whose only row is an advanced setting
 */
function makeAdvancedCard(): CardData {
  return {
    _info: { group: "Card", arg: "_info", card: "card-Main-Main", name: "Card" },
    Scheduler: {
      Enable: arg("Main", "Enable", "enable", true, { name: "Scheduler" }),
      NextRun: arg("Main", "NextRun", "input", "2026-11-26 08:00:00", { name: "Next Run" }),
      Server: arg("Main", "Server", "select", "cn", { name: "Server", option: ["cn", "en"], advanced: true }),
    },
    Settings: {
      EnableTask: arg("Main", "EnableTask", "checkbox", true, { name: "Enable Task" }),
      // A plain row whose help stays folded behind the question mark
      Mode: arg("Main", "Mode", "select", "normal", {
        name: "Mode",
        option: ["normal", "hard"],
        fold_help: true,
        help: "Folded help of a plain row",
      }),
      AmbushEvade: arg("Main", "AmbushEvade", "checkbox", true, { name: "Ambush Evade", advanced: true }),
    },
    Tuning: {
      // An advanced row with a folded help of its own: the toggle opens both
      Threshold: arg("Main", "Threshold", "input-int", 3, {
        name: "Threshold",
        advanced: true,
        fold_help: true,
        help: "Folded help of an advanced row",
      }),
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

/** The advanced settings toggle of the card header */
function advancedToggle(target: HTMLElement): HTMLButtonElement | undefined {
  return [...target.querySelectorAll<HTMLButtonElement>("button")].find((button) =>
    button.textContent?.includes(t.Input.Advanced()),
  );
}

/** The wrapper of the toggle whose ::after draws the underline */
function underlineOf(button: HTMLButtonElement): HTMLElement {
  return button.querySelector("span")!;
}

/** The question mark that folds the help of a row */
function helpToggle(target: HTMLElement): HTMLButtonElement | undefined {
  return [...target.querySelectorAll<HTMLButtonElement>("button")].find(
    (button) => button.getAttribute("aria-label") === t.Input.ShowHelp(),
  );
}

/** The reset button of the card header, the other hover action beside the toggle */
function resetButton(target: HTMLElement): HTMLButtonElement {
  return [...target.querySelectorAll<HTMLButtonElement>("button")].find((button) =>
    button.textContent?.includes(t.Input.GroupResetTitle()),
  )!;
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

describe("TestArgCardAdvanced", () => {
  it("keeps the advanced rows out of the card until the toggle is opened", async () => {
    const target = await mountCard(makeAdvancedCard());

    expect(target.textContent).toContain("Enable Task");
    expect(target.textContent).not.toContain("Ambush Evade");
    expect(target.textContent).not.toContain("Threshold");
    // scheduler args are hidden by the same toggle
    expect(target.textContent).not.toContain("Server");
  });

  it("reveals them on a click, each with a pill of its own", async () => {
    const target = await mountCard(makeAdvancedCard());
    advancedToggle(target)!.click();
    await settle();

    expect(target.textContent).toContain("Ambush Evade");
    expect(target.textContent).toContain("Threshold");
    expect(target.textContent).toContain("Server");
    const badges = [...target.querySelectorAll('[data-slot="badge"]')];
    expect(badges.map((badge) => badge.textContent?.trim())).toEqual([
      t.Input.Advanced(),
      t.Input.Advanced(),
      t.Input.Advanced(),
    ]);
  });

  it("hides them again on a second click", async () => {
    const target = await mountCard(makeAdvancedCard());
    advancedToggle(target)!.click();
    await settle();
    advancedToggle(target)!.click();
    await settle();

    expect(target.textContent).toContain("Enable Task");
    expect(target.textContent).not.toContain("Ambush Evade");
    expect(target.querySelector('[data-slot="badge"]')).toBeNull();
  });

  it("marks the toggle as open with primary text and an underline under the icon", async () => {
    const target = await mountCard(makeAdvancedCard());
    const closed = advancedToggle(target)!;
    // outside a card hover the toggle is transparent and out of the way
    expect(closed.classList.contains("opacity-0")).toBe(true);
    expect(closed.classList.contains("pointer-events-none")).toBe(true);
    expect(underlineOf(closed).classList.contains("after:opacity-100")).toBe(false);

    closed.click();
    await settle();

    const opened = advancedToggle(target)!;
    expect(opened.getAttribute("aria-pressed")).toBe("true");
    expect(opened.classList.contains("text-primary")).toBe(true);
    // the fill belongs to the Enable badge, the toggle never competes with it
    expect(opened.classList.contains("bg-primary")).toBe(false);
    expect(opened.classList.contains("rounded-full")).toBe(false);
    expect(opened.classList.contains("opacity-100")).toBe(true);
    expect(opened.classList.contains("opacity-0")).toBe(false);
    expect(opened.classList.contains("pointer-events-none")).toBe(false);
    // the rule is drawn by the wrapper of icon and label, so it runs under both
    expect(underlineOf(opened).classList.contains("after:opacity-100")).toBe(true);
  });

  it("shifts the header icons down, off the line box centre", async () => {
    // the label ink of a 14px text sits below the line box centre, so an icon
    // centred on that box reads as too high next to it
    const target = await mountCard(makeAdvancedCard());
    const reset = resetButton(target);

    expect(reset.querySelector("svg")!.classList.contains("translate-y-px")).toBe(true);
    expect(advancedToggle(target)!.querySelector("svg")!.classList.contains("translate-y-px")).toBe(true);
  });

  it("keeps the label of the advanced pill next to the cog it belongs to", async () => {
    const target = await mountCard(makeAdvancedCard());
    advancedToggle(target)!.click();
    await settle();

    for (const badge of target.querySelectorAll('[data-slot="badge"]')) {
      expect(badge.querySelector("svg")).not.toBeNull();
      expect(badge.textContent?.trim()).toBe(t.Input.Advanced());
    }
  });

  it("keeps the open label responsive to hover", async () => {
    const target = await mountCard(makeAdvancedCard());
    const closed = advancedToggle(target)!;
    expect(closed.classList.contains("hover:text-primary/80")).toBe(false);

    closed.click();
    await settle();

    // an open toggle must still answer the pointer: the ghost variant would
    // either repaint the label as foreground text or leave it inert
    const opened = advancedToggle(target)!;
    expect(opened.classList.contains("hover:text-primary/80")).toBe(true);
    expect(opened.classList.contains("text-primary")).toBe(true);
  });

  it("gives the reset button the same rule, revealed on its own hover", async () => {
    const target = await mountCard(makeAdvancedCard());
    const reset = resetButton(target);
    const rule = underlineOf(reset);

    // the rule wraps icon and label, so it can run under both
    expect(rule.querySelector("svg")).not.toBeNull();
    expect(rule.classList.contains("after:absolute")).toBe(true);
    // hidden until the button itself is hovered, not when the card is
    expect(rule.classList.contains("after:opacity-0")).toBe(true);
    expect(rule.classList.contains("group-hover/button:after:opacity-100")).toBe(true);
    expect(rule.classList.contains("after:opacity-100")).toBe(false);
  });

  it("keeps the two hover buttons close, and their distance to the badge", async () => {
    const target = await mountCard(makeAdvancedCard());
    const toggle = advancedToggle(target)!;

    // Nothing sits between the two hover buttons, so only their own paddings
    // separate them: the narrower padding of the pair is what closes the gap.
    const pair = toggle.parentElement!;
    expect(pair.classList.contains("gap-x-1")).toBe(false);
    expect(pair.classList.contains("gap-x-0")).toBe(false);
    for (const button of [toggle, resetButton(target)]) {
      expect(button.classList.contains("px-1.5")).toBe(true);
      // the shadcn default of this size, which would widen the gap again
      expect(button.classList.contains("px-2.5")).toBe(false);
    }

    // The row gives that padding back on its other side, so the rightmost label
    // keeps the distance it had to the Enable badge.
    expect(pair.parentElement!.classList.contains("gap-x-2")).toBe(true);
  });

  it("keeps the other header buttons of the card on hover", async () => {
    const target = await mountCard(makeAdvancedCard());
    advancedToggle(target)!.click();
    await settle();

    // reset shares the header row but stays a hover action of its own
    const reset = resetButton(target);
    expect(reset.classList.contains("opacity-0")).toBe(true);
    expect(reset.classList.contains("group-hover/card:opacity-100")).toBe(true);
  });

  it("draws both header buttons with the same behaviour", async () => {
    const target = await mountCard(makeAdvancedCard());

    // one rule for the two of them: same wrapper, same hover reveal
    const rule = underlineOf(resetButton(target));
    const toggleRule = underlineOf(advancedToggle(target)!);
    expect([...rule.classList].sort()).toEqual([...toggleRule.classList].sort());

    // only the toggle carries a state of its own
    expect(resetButton(target).hasAttribute("aria-pressed")).toBe(false);
    expect(advancedToggle(target)!.getAttribute("aria-pressed")).toBe("false");
  });

  it("keeps the question mark of a folded help while the advanced settings are open", async () => {
    const target = await mountCard(makeAdvancedCard());
    const help = "Folded help of a plain row";

    // folded by default: only the question mark is on screen
    expect(target.textContent).not.toContain(help);
    expect(helpToggle(target)).not.toBeUndefined();

    advancedToggle(target)!.click();
    await settle();

    // opening the advanced settings expands the help …
    expect(target.textContent).toContain(help);
    // … and the question mark stays, so the row can be folded again
    expect(helpToggle(target)).not.toBeUndefined();
    helpToggle(target)!.click();
    await settle();

    expect(target.textContent).toContain("Enable Task");
    expect(target.textContent).not.toContain(help);
  });

  it("opens the folded help of an advanced row together with the row", async () => {
    const target = await mountCard(makeAdvancedCard());
    const help = "Folded help of an advanced row";

    // one question mark belongs to the plain row, the second one is behind the
    // toggle and can not be seen yet
    expect(target.querySelectorAll(`button[aria-label="${t.Input.ShowHelp()}"]`)).toHaveLength(1);
    expect(target.textContent).not.toContain(help);

    advancedToggle(target)!.click();
    await settle();

    // the row and its revealed help arrive in the same pass
    expect(target.textContent).toContain("Threshold");
    expect(target.textContent).toContain(help);
    expect(target.querySelectorAll(`button[aria-label="${t.Input.ShowHelp()}"]`)).toHaveLength(2);
  });

  it("folds the help back when the advanced settings close", async () => {
    const target = await mountCard(makeAdvancedCard());
    advancedToggle(target)!.click();
    await settle();
    expect(target.textContent).toContain("Folded help of a plain row");

    advancedToggle(target)!.click();
    await settle();

    expect(target.textContent).not.toContain("Folded help of a plain row");
    expect(helpToggle(target)).not.toBeUndefined();
  });

  it("keeps the separator of a group of advanced rows only out of sight", async () => {
    const target = await mountCard(makeAdvancedCard());
    expect(target.querySelectorAll("hr")).toHaveLength(1);

    advancedToggle(target)!.click();
    await settle();

    expect(target.querySelectorAll("hr")).toHaveLength(2);
  });

  it("offers no toggle on a card without an advanced arg", async () => {
    const planner = await mountCard(makeCard());
    expect(advancedToggle(planner)).toBeUndefined();

    // a dashboard group edits its args in a dialog, the card holds no row of its own
    const amount = await mountCard(makeAmountCard());
    expect(advancedToggle(amount)).toBeUndefined();
  });
});
