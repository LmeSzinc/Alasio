/**
 * Tests for the restart status banner (RestartStatus.svelte)
 *
 * The component derives its view (phase, listed configs) and the stop
 * progress from the Restart / Worker topics, or from the props the dev page
 * overrides: the tests drive it through the props, no topic data is involved.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RestartTopicLike, WORKER_STATE } from "$lib/components/aside/types";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import RestartStatus from "./RestartStatus.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/** Mounts the banner with explicit phase / worker states (the dev page path). */
async function mountStatus(phase: RestartPhase | null, workers: Record<string, WORKER_STATE>) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const component = mount(RestartStatus, { target, props: { phase, workers } });
  mounted.push(component);
  await flushEffects();
  return target;
}

beforeEach(() => {
  // The mounted component subscribes the topics: fake timers keep a pending
  // reconnect of the shared websocket client from firing into the next test.
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.clearAllMocks();
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

describe("TestRestartStatus", () => {
  it("renders nothing without a restart in progress", async () => {
    const target = await mountStatus(null, {});
    expect(target.textContent).toBe("");
  });

  it("shows the stop progress of the configs of the restart", async () => {
    const target = await mountStatus("stopping", { cfg_a: "restarting", cfg_b: "running" });
    expect(target.textContent).toContain("1/2");
    expect(target.textContent).toContain("cfg_b");
    expect(target.textContent).not.toContain("cfg_a");
  });

  it("does not treat a leftover 'resuming' as a config still to stop (F14)", async () => {
    // A restart takes over while the previous auto-resume queue is still
    // waiting: the backend collects those configs (they turn "restarting"),
    // but the Worker topic may still show them "resuming" for a moment. They
    // have no process: they are not part of the waiting list and they count
    // as stopped, like the configs the restart already stopped.
    const target = await mountStatus("stopping", {
      cfg_a: "restarting",
      cfg_b: "resuming",
      cfg_c: "running",
    });
    expect(target.textContent).toContain("2/3");
    expect(target.textContent).toContain("cfg_c");
    expect(target.textContent).not.toContain("cfg_b");
    expect(target.textContent).not.toContain("cfg_a");
  });

  it("counts a config parked by the update flow as stopped", async () => {
    // The update flow stops the workers the same way and parks them in
    // "updating" (no process, auto-resume after the restart): it is not a
    // config the stop still waits for, it counts as stopped like "restarting".
    const target = await mountStatus("stopping", {
      cfg_a: "updating",
      cfg_b: "running",
      cfg_c: "scheduler-waiting",
    });
    expect(target.textContent).toContain("1/3");
    expect(target.textContent).toContain("cfg_b");
    expect(target.textContent).toContain("cfg_c");
    expect(target.textContent).not.toContain("cfg_a");
  });

  it("does not claim a shutdown from the configs parked by an update", async () => {
    // Missing phase: the configs parked by a restart are the fallback signal
    // that the backend is shutting down. An update parks its configs while the
    // backend keeps running (it applies the files first), so the banner stays
    // hidden for them instead of announcing a shutdown.
    const restart = await mountStatus(null, { cfg_a: "restarting" });
    expect(restart.textContent).not.toBe("");

    const update = await mountStatus(null, { cfg_a: "updating" });
    expect(update.textContent).toBe("");
  });

  it("lists the auto-resume queue while the new backend starts it", async () => {
    const target = await mountStatus("resuming", {
      cfg_a: "resuming",
      cfg_b: "starting",
      cfg_c: "running",
    });
    expect(target.textContent).toContain("cfg_a");
    expect(target.textContent).toContain("cfg_b");
    expect(target.textContent).not.toContain("cfg_c");
  });
});
