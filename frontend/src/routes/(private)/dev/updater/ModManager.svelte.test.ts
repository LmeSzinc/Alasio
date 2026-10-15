/**
 * Tests for the mod card list (ModManager.svelte)
 *
 * The list renders the mods of the ModList topic with the commit history of
 * the ModHistory topic and the update states of the Update topic, or from the
 * props the dev page overrides: the tests drive it through the props, no topic
 * data is involved. The rpcs behind the buttons keep the topic of their
 * component and are asserted on the fake socket.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UPDATE_STATE, UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
import { type HistoryItem, type ModHistoryData, type ModOption } from "$lib/components/update/utils";
import { t } from "$lib/i18n";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import ModManager from "./ModManager.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

/**
 * jsdom has no layout, so the width the card column follows is reported by
 * hand here (the component measures its container with `use:elementSize`).
 */
class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];
  #callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.#callback = callback;
    ResizeObserverStub.instances.push(this);
  }

  observe() {}
  unobserve() {}
  disconnect() {}

  /** Reports the width of the container, as the browser does after a resize. */
  resize(width: number) {
    this.#callback([{ contentRect: { width, height: 600 } }] as any, this as any);
  }
}

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

const MODS: ModOption[] = [
  { value: "alasio", label: "alasio" },
  { value: "example_mod", label: "example_mod" },
];

/** A version as the pack records it: the 40 hex characters of a commit sha1. */
function sha(index: number): string {
  return `${index.toString(16).padStart(2, "0")}${"0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8".slice(2)}`;
}

function commit(version: string, title: string, detail = ""): HistoryItem {
  return { version, author: "LmeSzinc", time: 1786727000, title, detail };
}

/** A mod history with one commit per title, the newest one first. */
function pack(titles: string[]): ModHistoryData[string] {
  return { data: titles.map((title, index) => commit(sha(index), title)) };
}

/** One entry of the Update topic, as the backend reports it. */
function update(state: UPDATE_STATE, current = "", latest = "", error = "", checkedAt?: number): UpdateInfoLike {
  return {
    state,
    current_version: current,
    latest_version: latest,
    checked_at: checkedAt ?? Math.floor(Date.now() / 1000),
    error,
  };
}

async function mountManager(props: { mods?: ModOption[]; history?: ModHistoryData; update?: UpdateTopicLike }) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(ModManager, { target, props }));
  await flushEffects();
  return target;
}

/** textContent with the whitespace of the template collapsed. */
function text(element: HTMLElement): string {
  return (element.textContent ?? "").replace(/\s+/g, " ");
}

/** The mod cards, one per mod (the list has no other card). */
function cards(target: HTMLElement): HTMLElement[] {
  return Array.from(target.querySelectorAll<HTMLElement>('[data-slot="card"]'));
}

/** The centered column of the cards (the outer element measures the width). */
function column(target: HTMLElement): HTMLElement {
  return target.firstElementChild!.firstElementChild as HTMLElement;
}

function buttons(target: HTMLElement): HTMLButtonElement[] {
  return Array.from(target.querySelectorAll<HTMLButtonElement>("button"));
}

/** The button of the given label, or null. */
function button(target: HTMLElement, label: string): HTMLButtonElement | null {
  return buttons(target).find((element) => element.textContent?.trim() === label) ?? null;
}

/** The icon button of the given tooltip, or null (the icon buttons carry no text). */
function tooltipButton(target: HTMLElement, title: string): HTMLButtonElement | null {
  return buttons(target).find((element) => element.title === title) ?? null;
}

/** The pill of the state of a card (the second one: the version pill comes first). */
function statePill(card: HTMLElement): HTMLElement {
  return card.querySelectorAll<HTMLElement>('[data-slot="badge"]')[1];
}

// The states whose check is refused by the backend: the component offers the
// check button as a dead one ('checking' is cancelled instead, see below)
const NOT_CHECKABLE: UPDATE_STATE[] = [
  "no-supervisor",
  "unmanaged",
  "disabled",
  "queued",
  "stopping",
  "downloading",
  "updating",
];

/** Label of every state of the Update topic. */
const STATE_LABEL: Record<UPDATE_STATE, () => string> = {
  "no-supervisor": t.Update.StateNoSupervisor,
  unmanaged: t.Update.StateUnmanaged,
  disabled: t.Update.StateDisabled,
  idle: t.Update.StateIdle,
  checking: t.Update.StateChecking,
  uptodate: t.Update.StateUptodate,
  available: t.Update.StateAvailable,
  queued: t.Update.StateQueued,
  stopping: t.Update.StateStopping,
  downloading: t.Update.StateDownloading,
  updating: t.Update.StateUpdating,
  "restart-required": t.Update.StateRestartRequired,
  error: t.Update.StateError,
};

/** Request payloads of a rpc the last socket sent, in order. */
function rpcCalls(func: string) {
  const ws = FakeWebSocket.last;
  if (!ws) return [];
  return ws.sent
    .map((raw) => JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw)))
    .filter((message) => message.o === "rpc" && message.f === func)
    .map((message) => message.t);
}

beforeEach(() => {
  // The mounted list subscribes the topics: fake timers keep a pending
  // reconnect of the shared websocket client from firing into the next test.
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.clearAllMocks();
  ResizeObserverStub.instances = [];
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  // Unmount every component the test mounted, even on assertion failure: a
  // leaked component keeps its effects (the topic subscriptions) alive.
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  websocketClient.disconnect();
  vi.useRealTimers();
});

describe("TestModManagerEmpty", () => {
  it("replaces the list with the placeholder without any mod", async () => {
    const target = await mountManager({ mods: [], history: {}, update: {} });

    expect(text(target)).toContain(t.Mod.NoMod());
    expect(cards(target)).toHaveLength(0);
    // no mod, no update entry: the update line and its buttons are gone too
    expect(button(target, t.Update.ButtonUpdate())).toBeNull();
  });
});

describe("TestModManagerHistory", () => {
  it("previews the newest commits and expands every one", async () => {
    const target = await mountManager({
      mods: MODS,
      history: { alasio: pack(["commit one", "commit two", "commit three", "commit four", "commit five"]) },
      update: {},
    });

    // the preview keeps the 3 newest commits, the rest waits for the button
    expect(text(target)).toContain("commit three");
    expect(text(target)).not.toContain("commit four");
    expect(button(target, t.Mod.ExpandAll())).not.toBeNull();

    button(target, t.Mod.ExpandAll())!.click();
    await flushEffects();

    expect(text(target)).toContain("commit five");
    expect(button(target, t.Mod.CollapseAll())).not.toBeNull();
  });

  it("has no expand button when every commit fits the preview", async () => {
    const target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: pack(["commit one", "commit two", "commit three"]) },
      update: {},
    });

    expect(text(target)).toContain("commit three");
    expect(button(target, t.Mod.ExpandAll())).toBeNull();
  });

  it("shows the empty and the error history of a mod", async () => {
    const target = await mountManager({
      mods: MODS,
      history: {
        alasio: { data: [] },
        example_mod: { data: [], error: "FileNotFoundError: 'example_mod/.pack/history.pack'" },
      },
      update: {},
    });

    const [alasio, exampleMod] = cards(target);
    expect(text(alasio)).toContain(t.Mod.NoHistory());
    expect(text(exampleMod)).toContain("FileNotFoundError: 'example_mod/.pack/history.pack'");
  });

  it("shortens a sha1 version, keeps a tag name and opens the detail of a commit", async () => {
    const target = await mountManager({
      mods: [MODS[0]],
      history: {
        alasio: {
          data: [
            commit(sha(0), "Add: history.pack in full pack", "The message body of the commit."),
            commit("v1.2.3", "Rel: 1.2.3"),
          ],
        },
      },
      update: {},
    });

    const card = cards(target)[0];
    // the pill of the title row is the version of the newest commit, shortened
    // to 8 characters like every sha1
    expect(card.querySelector('[data-slot="badge"]')!.textContent?.trim()).toBe(sha(0).slice(0, 8));
    // a version that is no sha1 (a tag) is kept whole
    expect(text(card)).toContain("v1.2.3");
    expect(text(card)).not.toContain(sha(1).slice(0, 8));

    // only the commit with a detail carries the expand button
    expect(tooltipButton(card, t.Mod.ExpandDetail())).not.toBeNull();
    expect(text(card)).not.toContain("The message body of the commit.");

    tooltipButton(card, t.Mod.ExpandDetail())!.click();
    await flushEffects();

    expect(text(card)).toContain("The message body of the commit.");
    expect(tooltipButton(card, t.Mod.CollapseDetail())).not.toBeNull();
  });
});

describe("TestModManagerUpdateStates", () => {
  it("renders every state of the Update topic with the buttons of that state", async () => {
    for (const state of Object.keys(STATE_LABEL) as UPDATE_STATE[]) {
      const target = await mountManager({
        mods: [MODS[0]],
        history: {},
        update: { alasio: update(state, "0beb992", "ab41c81", state === "error" ? "TimeoutError: no answer" : "") },
      });
      const card = cards(target)[0];

      // the pill of the state carries the label of that state
      expect(text(card)).toContain(STATE_LABEL[state]());
      if (state === "error") {
        // a failure reads red
        expect(statePill(card).className).toContain("bg-destructive");
        expect(text(card)).toContain("TimeoutError: no answer");
      }

      if (state === "checking") {
        // a check in flight is cancelled instead of started again
        expect(button(card, t.Update.ButtonCancel())).not.toBeNull();
        expect(button(card, t.Update.ButtonCheck())).toBeNull();
        continue;
      }

      const check = button(card, t.Update.ButtonCheck());
      expect(check).not.toBeNull();
      // a check the backend refuses is offered as a dead button
      expect(check!.disabled).toBe(NOT_CHECKABLE.includes(state));
    }
  });

  it("spells the checked time and the version in the state pill", async () => {
    // up to date: the pill carries the moment of the last finished check
    const now = Math.floor(Date.now() / 1000);
    let target = await mountManager({
      mods: [MODS[0]],
      history: {},
      update: { alasio: update("uptodate", "ab41c81", "ab41c81", "", now - 20) },
    });
    expect(text(cards(target)[0])).toContain(`${t.Update.StateUptodate()} - ${t.Dashboard.JustNow()}`);

    target = await mountManager({
      mods: [MODS[0]],
      history: {},
      update: { alasio: update("uptodate", "ab41c81", "ab41c81", "", now - 5 * 60) },
    });
    expect(text(cards(target)[0])).toContain(`${t.Update.StateUptodate()} - ${t.Dashboard.MinutesAgo({ time: 5 })}`);

    // available: the pill carries the version the update brings, shortened when
    // it is the sha1 of a pack
    target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: pack(["commit one"]) },
      update: { alasio: update("available", "0beb992", sha(3)) },
    });
    expect(text(cards(target)[0])).toContain(`${t.Update.StateAvailable()} - ${sha(3).slice(0, 8)}`);
    // the pill of the version of the pack is next to it
    expect(text(cards(target)[0])).toContain(sha(0).slice(0, 8));
  });

  it("keeps both pills, and the check button, without an update entry", async () => {
    const target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: pack(["commit one"]) },
      update: {},
    });

    const card = cards(target)[0];
    // the version pill is the version of the newest commit, the state pill
    // reads like a mod waiting for its first check
    expect(text(card)).toContain(sha(0).slice(0, 8));
    expect(text(card)).toContain(t.Update.StateIdle());
    expect(button(card, t.Update.ButtonCheck())!.disabled).toBe(false);
  });

  it("keeps both pills for a mod without any version of its own", async () => {
    const target = await mountManager({ mods: [MODS[0]], history: {}, update: {} });
    const card = cards(target)[0];

    // the pills of the title row are always there: the version one reads
    // 'none' when no history and no update entry reports a version
    expect(card.querySelectorAll('[data-slot="badge"]')).toHaveLength(2);
    expect(statePill(card).previousElementSibling!.textContent?.trim()).toBe(t.Update.VersionUnavailable());
  });

  it("takes the version of the pack from the update entry without a history", async () => {
    const target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: { data: [] } },
      update: { alasio: update("uptodate", sha(2), sha(2)) },
    });

    // the version pill falls back to the version the update entry reports
    expect(text(cards(target)[0])).toContain(sha(2).slice(0, 8));
  });

  it("flashes the state pill when the checked time changes, never on load", async () => {
    const updateData = $state<UpdateTopicLike>({ alasio: update("uptodate", "ab41c81", "ab41c81", "", 1000) });
    const target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: pack(["commit one"]) },
      update: updateData,
    });
    const pill = () => statePill(cards(target)[0]);

    // the value pushed when the page opens reads as its own state, without a flash
    expect(pill().className).toContain("bg-secondary");
    expect(pill().className).not.toContain("bg-primary");

    // a finished check (a new checked time) flashes the primary background
    updateData.alasio = update("uptodate", "ab41c81", "ab41c81", "", 2000);
    await flushEffects();
    expect(pill().className).toContain("bg-primary");

    // and the pill is back to its normal look one second later
    vi.advanceTimersByTime(1000);
    await flushEffects();
    expect(pill().className).toContain("bg-secondary");
    expect(pill().className).not.toContain("bg-primary");
  });

  it("keeps the failure message right below the title row", async () => {
    const target = await mountManager({
      mods: [MODS[0]],
      history: { alasio: pack(["commit one"]) },
      update: { alasio: update("error", "0beb992", "ab41c81", "Some files failed to update, retry the update") },
    });

    const card = cards(target)[0];
    const header = card.querySelector<HTMLElement>('[data-slot="card-header"]')!;
    const content = card.querySelector<HTMLElement>('[data-slot="card-content"]')!;
    // the message belongs to the title row: it lives in the header, right after
    // the row of the title, and the history below keeps the gap of the card
    const titleRow = header.firstElementChild as HTMLElement;
    const message = titleRow.nextElementSibling as HTMLElement;
    expect(text(message)).toBe("Some files failed to update, retry the update");
    expect(text(content)).not.toContain("Some files failed to update");
    expect(text(content).startsWith(t.Mod.Version())).toBe(true);
  });
});

describe("TestModManagerUpdateLine", () => {
  it("counts the available updates and applies them from the single entry", async () => {
    const target = await mountManager({
      mods: MODS,
      history: {},
      update: {
        alasio: update("available", "0beb992", "ab41c81"),
        example_mod: update("uptodate", "ab41c81", "ab41c81"),
      },
    });
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    expect(text(target)).toContain(t.Update.AvailableCount({ count: "1" }));
    const apply = button(target, t.Update.ButtonUpdate())!;
    expect(apply.disabled).toBe(false);

    // no confirmation dialog: the update applies every available mod at once
    // (the user reached this page from the popup announcing the update)
    apply.click();
    await flushEffects();
    expect(rpcCalls("update_apply")).toEqual(["Update"]);
    expect(document.querySelector('[data-slot="alert-dialog-content"]')).toBeNull();
  });

  it("explains why no update is offered instead of blaming the mods", async () => {
    // the mod updates are off for this backend
    let target = await mountManager({
      mods: MODS,
      history: {},
      update: { alasio: update("disabled", "0beb992"), example_mod: update("disabled", "0beb992") },
    });
    expect(text(target)).toContain(t.Update.UpdatesDisabled());
    expect(button(target, t.Update.ButtonUpdate())!.disabled).toBe(true);

    // the backend runs without a live supervisor
    target = await mountManager({ mods: MODS, history: {}, update: { alasio: update("no-supervisor", "0beb992") } });
    expect(text(target)).toContain(t.Update.UpdatesNoSupervisor());

    // every managed mod is up to date
    target = await mountManager({
      mods: MODS,
      history: {},
      update: { alasio: update("uptodate", "ab41c81", "ab41c81") },
    });
    expect(text(target)).toContain(t.Update.NoAvailable());
    expect(button(target, t.Update.ButtonUpdate())!.disabled).toBe(true);
  });

  it("stands the update buttons at the left of the line, before the state", async () => {
    const target = await mountManager({ mods: MODS, history: {}, update: { alasio: update("downloading") } });
    // the line of the single update entry is the first box of the column
    const line = column(target).firstElementChild as HTMLElement;

    // the buttons come first, the update one then the cancel one
    expect([...line.querySelectorAll("button")].map((b) => b.textContent?.trim())).toEqual([
      t.Update.ButtonUpdate(),
      t.Update.ButtonCancel(),
    ]);
    // and the state of the updates follows the two buttons
    const parts = [...line.children].map((child) => text(child as HTMLElement));
    expect(parts).toEqual([`${t.Update.ButtonUpdate()} ${t.Update.ButtonCancel()}`, t.Update.NoAvailable()]);
  });

  it("cancels a flow that may still be cancelled through the rpc", async () => {
    const target = await mountManager({ mods: [MODS[0]], history: {}, update: { alasio: update("downloading") } });
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    const cancel = button(target, t.Update.ButtonCancel());
    expect(cancel).not.toBeNull();
    cancel!.click();
    await flushEffects();

    expect(rpcCalls("cancel_apply")).toEqual(["Update"]);
  });

  it("leaves a flow past the point of no return alone", async () => {
    const target = await mountManager({ mods: [MODS[0]], history: {}, update: { alasio: update("updating") } });
    expect(button(target, t.Update.ButtonCancel())).toBeNull();
  });
});

describe("TestModManagerCardColumn", () => {
  it("follows the card width of the config pages", async () => {
    const target = await mountManager({ mods: MODS, history: { alasio: pack(["commit one"]) }, update: {} });
    const box = column(target);

    // the column is centered and follows the container until max-w-180
    expect(box.classList.contains("mx-auto")).toBe(true);
    expect(box.classList.contains("max-w-180")).toBe(true);
    // every card of the list sits in the same column
    expect(box.querySelectorAll('[data-slot="card"]')).toHaveLength(MODS.length);

    // a wide container: the cards keep the 3/5 ratio, then stop at max-w-240
    ResizeObserverStub.instances[0].resize(1400);
    await flushEffects();
    expect(box.classList.contains("w-3/5")).toBe(true);

    ResizeObserverStub.instances[0].resize(1700);
    await flushEffects();
    expect(box.classList.contains("max-w-240")).toBe(true);

    for (const card of cards(target)) {
      expect(card.classList.contains("neushadow")).toBe(true);
      expect(card.classList.contains("border-none")).toBe(true);
    }
  });
});
