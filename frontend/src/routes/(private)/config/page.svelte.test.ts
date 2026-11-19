/**
 * Tests for the `/config` route without a configuration name
 * ((private)/config/+page.svelte)
 *
 * The route is a way to say "open whatever the app opens", so it must resolve to
 * the first configuration through the same resolver as the app root instead of
 * falling through to the router's 404 page.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goto } from "$app/navigation";
import type { ConfigLike, ConfigTopicLike } from "$lib/components/aside/types";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import Page from "./+page.svelte";

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

/** Mount the route, accept its subscription and push a config list. */
async function openRoute(configs: ConfigTopicLike) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(Page, { target }));
  await flushEffects();
  FakeWebSocket.last!.serverOpen();
  await flushEffects();

  FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "ConfigScan", o: "full", v: configs }));
  await flushEffects();
  return target;
}

describe("TestConfigRouteWithoutName", () => {
  it("opens the first configuration instead of a 404", async () => {
    await openRoute({ b: config("cfg_b", 2, 1), a: config("cfg_a", 1, 1) });

    expect(goto).toHaveBeenCalledTimes(1);
    expect(goto).toHaveBeenCalledWith("/config/cfg_a/overview", { replaceState: true });
  });

  it("paints nothing while the list is on its way", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    mounted.push(mount(Page, { target }));
    await flushEffects();
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    expect(goto).not.toHaveBeenCalled();
    expect((target.textContent ?? "").trim()).toBe("");
  });
});
