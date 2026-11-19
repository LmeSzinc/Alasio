/**
 * Tests for the entry resolver ((private)/Entry.svelte)
 *
 * The component has no view of its own: it resolves where the app opens and
 * draws nothing while it does. A fresh install has no configuration until the
 * backend finished creating its default one, so the list may arrive after the
 * component mounted; the redirect must happen whenever it arrives, and a backend
 * that will never have one must not leave the page waiting forever.
 *
 * Both routes without a configuration render it: the app root and `/config`.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goto } from "$app/navigation";
import type { ConfigLike, ConfigTopicLike } from "$lib/components/aside/types";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import Entry, { FALLBACK_DELAY } from "./Entry.svelte";

// The ws framework imports svelte-sonner for rpc toasts; the resolver only
// reads a topic, keep the real toast library out of the jsdom tests.
vi.mock("svelte-sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.stubGlobal("WebSocket", FakeWebSocket);

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** A configuration as the ConfigScan topic carries it. */
function config(name: string, gid: number, iid: number): ConfigLike {
  return { id: 0, name, mod: "TestMod", gid, iid };
}

/** Mount the resolver and accept the subscription of its topic. */
async function mountEntry() {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(Entry, { target }));
  await flushEffects();
  FakeWebSocket.last!.serverOpen();
  await flushEffects();
  return target;
}

/** Push a full snapshot of the ConfigScan topic, as the backend does. */
async function pushConfigs(configs: ConfigTopicLike) {
  FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "ConfigScan", o: "full", v: configs }));
  await flushEffects();
}

/** Text the component paints, whitespace collapsed. */
function text(target: HTMLElement): string {
  return (target.textContent ?? "").replace(/\s+/g, " ").trim();
}

beforeEach(() => {
  // the resolver arms a fallback timer: fake timers keep it, and a pending
  // reconnect of the shared websocket client, out of the next test
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.mocked(goto).mockClear();
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
  websocketClient.disconnect();
  vi.useRealTimers();
});

describe("TestEntryRendering", () => {
  it("paints nothing while it waits", async () => {
    const target = await mountEntry();

    // no spinner, no text: the shell of the layout is all the user sees, so the
    // redirect never flashes a message away
    expect(text(target)).toBe("");
    expect(target.querySelector("svg")).toBeNull();
  });

  it("keeps painting nothing after the configuration arrives", async () => {
    const target = await mountEntry();

    await pushConfigs({ a: config("cfg_a", 1, 1) });

    expect(text(target)).toBe("");
  });
});

describe("TestEntryNavigation", () => {
  it("does not navigate while the scan has not arrived", async () => {
    await mountEntry();

    expect(goto).not.toHaveBeenCalled();
  });

  it("opens the first configuration when the scan arrives", async () => {
    await mountEntry();

    // the default configuration the backend created after the component mounted
    await pushConfigs({ b: config("cfg_b", 2, 1), a: config("cfg_a", 1, 2), c: config("cfg_c", 1, 1) });

    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/config/cfg_c/overview", { replaceState: true });
  });

  it("opens the configuration only once when the topic pushes again meanwhile", async () => {
    await mountEntry();

    await pushConfigs({ a: config("cfg_a", 1, 1) });
    // a rescan (e.g. the default configuration of the next mod) lands while the
    // navigation of the first one is still running
    await pushConfigs({ a: config("cfg_a", 1, 1), b: config("cfg_b", 1, 2) });

    expect(goto).toHaveBeenCalledTimes(1);
  });
});

describe("TestEntryNoConfig", () => {
  it("keeps waiting while the default configuration may still be created", async () => {
    await mountEntry();

    await vi.advanceTimersByTimeAsync(FALLBACK_DELAY / 2);
    await flushEffects();

    expect(goto).not.toHaveBeenCalled();
  });

  it("hands the user to the config manager when no configuration appears", async () => {
    const target = await mountEntry();

    await vi.advanceTimersByTimeAsync(FALLBACK_DELAY);
    await flushEffects();

    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/dev/config", { replaceState: true });
    // the fallback is silent: the page it leaves behind still paints nothing
    expect(text(target)).toBe("");
  });

  it("does not leave for the config manager once a configuration was opened", async () => {
    await mountEntry();

    await pushConfigs({ a: config("cfg_a", 1, 1) });
    await vi.advanceTimersByTimeAsync(FALLBACK_DELAY);
    await flushEffects();

    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/config/cfg_a/overview", { replaceState: true });
  });

  it("ignores a configuration that arrives after the config manager took over", async () => {
    await mountEntry();

    await vi.advanceTimersByTimeAsync(FALLBACK_DELAY);
    await flushEffects();
    await pushConfigs({ a: config("cfg_a", 1, 1) });

    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/dev/config", { replaceState: true });
  });
});
