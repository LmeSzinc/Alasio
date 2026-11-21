/**
 * Tests for the config manager page ((private)/dev/config/ConfigScan.svelte)
 *
 * The page is a rendering of the ConfigScan topic plus the dialogs that edit
 * it. The websocket layer is mocked (fake-topic.svelte.ts): the tests set the
 * topic data and read the rpc calls the page makes, so they are about what the
 * page shows and what it asks the backend to do, without a connection. The dnd
 * gestures themselves cannot be driven in jsdom (dnd-kit needs a laid out
 * page); the drop logic is covered by configDnd.test.ts.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConfigLike, ConfigTopicLike } from "$lib/components/aside/types";
import { type FakeRpcCall, fakeTopic, resetFakeTopics } from "$lib/test-utils/fake-topic.svelte";
import { flushEffects } from "$lib/test-utils/flush-effects";
import ConfigScan from "./ConfigScan.svelte";

// The page and the mod selector read a topic; give them the fake one.
vi.mock("$lib/ws", async () => {
  const { useFakeTopic } = await import("$lib/test-utils/fake-topic.svelte");
  return { useTopic: useFakeTopic };
});

/**
 * jsdom has no ResizeObserver; the floating layers of bits-ui observe their
 * content, the stub only has to exist.
 */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/** Ids of the fixtures, unique over a test. */
let nextId = 991;

beforeEach(() => {
  nextId = 991;
  resetFakeTopics();
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

/** mount() is generic over the component's props/exports, so its return type
 * cannot be spelled with ReturnType; the array only holds component handles
 * for unmount. */
let mounted: any[] = [];

/** A configuration as the ConfigScan topic carries it. */
function config(name: string, gid: number, iid: number): ConfigLike {
  return { id: nextId++, name, mod: "TestMod", gid, iid };
}

/** The topic the page subscribes to. */
function scanTopic() {
  return fakeTopic<ConfigTopicLike>("ConfigScan");
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

/** Mounts the page. */
async function mountPage(): Promise<HTMLElement> {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(ConfigScan, { target }));
  await settle();
  return target;
}

/** Publishes a snapshot of the topic, as a pushed `full` event would. */
async function push(configs: ConfigTopicLike) {
  scanTopic().data = configs;
  await settle();
}

/** The rpc calls the page made on the ConfigScan topic. */
function calls(): FakeRpcCall[] {
  return scanTopic().calls;
}

/** The roots of the groups, in render order. */
function groupRoots(target: HTMLElement): HTMLElement[] {
  return [...target.querySelectorAll<HTMLElement>(".drag-placeholder")].filter((el) =>
    el.firstElementChild?.classList.contains("group-container"),
  );
}

/** The name a config row shows (a group header carries no mono name). */
function textOf(row: HTMLElement): string {
  return row.querySelector(".font-mono")?.textContent?.trim() ?? "";
}

/** The config names of every group, group by group, in render order. */
function groupNames(target: HTMLElement): string[][] {
  return groupRoots(target).map((group) =>
    [...group.querySelectorAll<HTMLElement>(".drag-placeholder")]
      .filter((el) => el.firstElementChild?.classList.contains("bg-card"))
      .map((el) => textOf(el)),
  );
}

/** The numbers of the group headers, in render order. */
function groupNumbers(target: HTMLElement): string[] {
  return [...target.querySelectorAll("span")].flatMap((el) => {
    const match = (el.textContent?.trim() ?? "").match(/#(\d+)$/);
    return match ? [match[1]] : [];
  });
}

/** The row of a config, with the actions it carries. */
function rowFor(target: HTMLElement, name: string): HTMLElement | undefined {
  return [...target.querySelectorAll<HTMLElement>(".drag-placeholder")].find(
    (el) => el.firstElementChild?.classList.contains("bg-card") && textOf(el) === name,
  );
}

/** The button of an action, found by the icon it shows. */
function actionButton(row: HTMLElement, icon: string): HTMLButtonElement | undefined {
  return [...row.querySelectorAll("button")].find((button) => button.querySelector(`svg.lucide-${icon}`));
}

/** The open dialog, if any. */
function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="dialog-content"]');
}

/** The confirm button of the open dialog: the footer's last one. */
function confirmButton(): HTMLButtonElement | undefined {
  const footer = dialog()?.querySelector('[data-slot="dialog-footer"]');
  return footer ? [...footer.querySelectorAll<HTMLButtonElement>("button")].at(-1) : undefined;
}

/** Types into an input the way a user does. */
async function type(input: HTMLInputElement, value: string) {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await settle();
}

describe("TestConfigScanWaiting", () => {
  it("waits for the topic data", async () => {
    const target = await mountPage();

    expect(target.textContent).toContain("Waiting for configuration data...");
    expect(groupRoots(target)).toEqual([]);
  });

  it("stops waiting once the first snapshot arrives", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    expect(target.textContent).not.toContain("Waiting for configuration data...");
    expect(groupNames(target)).toEqual([["a"]]);
  });
});

describe("TestConfigScanRender", () => {
  it("renders the groups by gid and the configs of a group by iid", async () => {
    const target = await mountPage();
    // The dictionary order means nothing, the layout comes from gid and iid
    await push({
      b2: config("b2", 2, 2),
      a1: config("a1", 1, 1),
      b1: config("b1", 2, 1),
      a2: config("a2", 1, 2),
    });

    expect(groupNames(target)).toEqual([
      ["a1", "a2"],
      ["b1", "b2"],
    ]);
  });

  it("shows the mod of every config", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    expect(rowFor(target, "a")!.textContent).toContain("TestMod");
  });

  it("numbers the groups by their position, not by their gid", async () => {
    const target = await mountPage();
    // A snapshot may carry any gid, the header always counts the groups
    await push({ a: config("a", 3, 1), b: config("b", 7, 1) });

    expect(groupNumbers(target)).toEqual(["1", "2"]);
  });

  it("follows a new snapshot of the topic", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1), b: config("b", 2, 1), c: config("c", 3, 1) });
    expect(groupNames(target)).toEqual([["a"], ["b"], ["c"]]);

    // The backend renumbered the groups: this is the state a page reload shows
    await push({ a: config("a", 1, 2), b: config("b", 1, 1), c: config("c", 2, 1) });

    expect(groupNames(target)).toEqual([["b", "a"], ["c"]]);
  });

  it("drops a group the snapshot no longer has", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1), b: config("b", 2, 1) });
    expect(groupRoots(target)).toHaveLength(2);

    await push({ a: config("a", 1, 1) });

    expect(groupNames(target)).toEqual([["a"]]);
  });

  it("sends nothing on its own", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1), b: config("b", 2, 1) });

    // Rendering a snapshot is not an edit
    expect(calls()).toEqual([]);
  });
});

describe("TestConfigScanAddDialog", () => {
  it("adds a config from the header button", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });
    // The mod list the dialog offers to choose from
    fakeTopic<{ value: string; label: string }[]>("ModList").data = [{ value: "TestMod", label: "TestMod" }];
    await settle();

    const add = [...target.querySelectorAll("button")].find((button) => button.querySelector("svg.lucide-plus"));
    expect(add).toBeDefined();
    add!.click();
    await settle();
    expect(dialog()).not.toBeNull();

    await type(dialog()!.querySelector<HTMLInputElement>("#config-name")!, " new_config ");
    confirmButton()!.click();
    await settle();

    // The name is trimmed, the mod is the one the selector fell back to
    expect(calls()).toEqual([
      { topic: "ConfigScan", func: "config_add", args: { name: "new_config", mod: "TestMod" } },
    ]);
  });

  it("keeps the add button disabled without a name", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    const add = [...target.querySelectorAll("button")].find((button) => button.querySelector("svg.lucide-plus"));
    add!.click();
    await settle();

    expect(confirmButton()!.disabled).toBe(true);
  });
});

describe("TestConfigScanRowActions", () => {
  it("deletes the config named in the dialog", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1), b: config("b", 2, 1) });

    actionButton(rowFor(target, "b")!, "trash-2")!.click();
    await settle();

    // The dialog names the config it is about to delete, in its own row
    expect(dialog()!.querySelector(".font-mono")?.textContent?.trim()).toBe("b");

    confirmButton()!.click();
    await settle();

    expect(calls()).toEqual([{ topic: "ConfigScan", func: "config_del", args: { name: "b" } }]);
  });

  it("renames a config with the name typed in the dialog", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    actionButton(rowFor(target, "a")!, "file-pen-line")!.click();
    await settle();

    const input = dialog()!.querySelector<HTMLInputElement>("#rename-config-name")!;
    expect(input.value).toBe("a");
    await type(input, "renamed");
    confirmButton()!.click();
    await settle();

    expect(calls()).toEqual([
      { topic: "ConfigScan", func: "config_rename", args: { old_name: "a", new_name: "renamed" } },
    ]);
  });

  it("copies a config to the name typed in the dialog", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    actionButton(rowFor(target, "a")!, "copy")!.click();
    await settle();

    await type(dialog()!.querySelector<HTMLInputElement>("#new-config-name")!, "a_copy");
    confirmButton()!.click();
    await settle();

    expect(calls()).toEqual([
      { topic: "ConfigScan", func: "config_copy", args: { old_name: "a", new_name: "a_copy" } },
    ]);
  });

  it("sends nothing when the dialog is cancelled", async () => {
    const target = await mountPage();
    await push({ a: config("a", 1, 1) });

    actionButton(rowFor(target, "a")!, "trash-2")!.click();
    await settle();
    const cancel = [...dialog()!.querySelectorAll("button")].find((button) =>
      button.textContent?.trim().match(/^(Cancel|取消)$/),
    );
    expect(cancel).toBeDefined();
    cancel!.click();
    await settle();

    expect(dialog()).toBeNull();
    expect(calls()).toEqual([]);
    // Every dialog is closed again
    expect(scanTopic().rpcs.every((rpc) => !rpc.isOpen)).toBe(true);
  });
});
