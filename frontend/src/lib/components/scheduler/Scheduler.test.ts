/**
 * Tests for the scheduler card (Scheduler.svelte)
 *
 * The worker state picks the button row of the card and, for a worker that is
 * stopping or parked for a graceful backend restart, the Restart topic phase
 * picks the variant within that row. The tests drive the phase through the
 * prop the dev page overrides; the prop-less mounts cover the production path
 * (the phase comes from the topic then).
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WORKER_STATE } from "$lib/components/aside/types";
import { t } from "$lib/i18n";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import Scheduler from "./Scheduler.svelte";
import type { RestartPhase } from "./types";

vi.stubGlobal("WebSocket", FakeWebSocket);

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/**
 * Mounts the card; an undefined phase reads the Restart topic like production
 */
async function mountScheduler(workerState: WORKER_STATE, restartPhase?: RestartPhase | null) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const component = mount(Scheduler, {
    target,
    props: { config_name: "TestConfig", workerState, restartPhase },
  });
  mounted.push(component);
  await flushEffects();
  return target;
}

/** Buttons of the card in render order (an icon-only button has no text) */
function buttons(target: HTMLElement) {
  return Array.from(target.querySelectorAll("button"));
}

/** Labels of the buttons, icon-only ones keep an empty label */
function labels(target: HTMLElement) {
  return buttons(target).map((button) => button.textContent?.trim() ?? "");
}

/** True when the button renders the lucide icon of that name */
function hasIcon(button: HTMLElement, name: string) {
  return button.querySelector(`svg.lucide-${name}`) !== null;
}

/**
 * True when the button is a bits-ui tooltip trigger (it marks one with a
 * `data-tooltip-trigger` attribute, also while the tooltip is closed)
 */
function hasTooltip(button: HTMLElement) {
  return button.hasAttribute("data-tooltip-trigger");
}

/** Args of the kill rpcs the last socket sent, in order */
function killCalls() {
  const ws = FakeWebSocket.last;
  if (!ws) return [];
  return ws.sent
    .map((raw) => JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw)))
    .filter((message) => message.t === "Worker" && message.o === "rpc" && message.f === "kill")
    .map((message) => message.v);
}

beforeEach(() => {
  // The mounted card subscribes the topics: fake timers keep a pending
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

describe("TestSchedulerStopButtons", () => {
  it("keeps the plain scheduler stop without a restart", async () => {
    const target = await mountScheduler("scheduler-stopping", null);
    const [kill, continueRunning] = buttons(target);
    expect(labels(target)).toEqual([t.Scheduler.Kill(), ""]);
    expect(hasIcon(continueRunning, "play")).toBe(true);

    // Both buttons are debounced for a second after the worker entered the
    // state, so a double click cannot send a second command
    expect(kill.disabled).toBe(true);
    expect(continueRunning.disabled).toBe(true);
    vi.advanceTimersByTime(1000);
    await flushEffects();
    expect(kill.disabled).toBe(false);
    expect(continueRunning.disabled).toBe(false);
  });

  it("swaps in the restart variants during a restart", async () => {
    // Both phases of the graceful stop (the workers are still stopping during
    // 'shutting-down') and a leftover stopping worker while the new backend
    // resumes keep the restart variants: a stop now still has a resume intent
    // to keep or to cancel. The row keeps the shape of the other states: one
    // wide button and one round X.
    for (const phase of ["stopping", "shutting-down", "resuming"] as const) {
      const target = await mountScheduler("scheduler-stopping", phase);
      const [keepResume, noResume] = buttons(target);
      expect(labels(target)).toEqual([t.Scheduler.KillKeepResume(), ""]);
      expect(hasIcon(noResume, "x")).toBe(true);
      // a stopping worker is not in the frozen resume list: both are live
      expect(keepResume.disabled).toBe(false);
      expect(noResume.disabled).toBe(false);
    }
  });

  it("stops a stopping worker with or without the resume from the restart row", async () => {
    const target = await mountScheduler("scheduler-stopping", "stopping");
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    const [keepResume, noResume] = buttons(target);
    // the wide button force-stops and keeps the auto-resume
    keepResume.click();
    await flushEffects();
    // the round X stops without resuming: the default kill cancels it
    noResume.click();
    await flushEffects();

    expect(killCalls()).toEqual([{ config: "TestConfig", restart_resume: true }, { config: "TestConfig" }]);
  });

  it("goes back to the plain stop once the restart is done", async () => {
    // 'done' is transient (pushed right before the topic is cleared) and does
    // not count as a restart in progress
    const target = await mountScheduler("scheduler-stopping", "done");
    expect(labels(target)).toEqual([t.Scheduler.Kill(), ""]);
    expect(hasIcon(buttons(target)[1], "play")).toBe(true);
  });
});

describe("TestSchedulerCancelResume", () => {
  it("cancels the auto-resume of a stopped worker", async () => {
    const target = await mountScheduler("restarting", "stopping");
    const [start, cancel] = buttons(target);
    expect(start.textContent?.trim()).toBe(t.Scheduler.Start());
    expect(start.disabled).toBe(true);
    expect(hasIcon(cancel, "x")).toBe(true);
    expect(cancel.disabled).toBe(false);
  });

  it("refuses the cancel once the resume list is frozen (F4)", async () => {
    // 'shutting-down' is the point of no return: the resume file is written
    // from the frozen list, the backend refuses a cancel from there on
    const target = await mountScheduler("restarting", "shutting-down");
    const [start, cancel] = buttons(target);
    expect(start.disabled).toBe(true);
    expect(hasIcon(cancel, "x")).toBe(true);
    expect(cancel.disabled).toBe(true);
  });

  it("cancels a queued resume of the new backend", async () => {
    // 'resuming' has no process yet, a stop always cancels the queue entry
    // (the frozen list of the previous restart does not apply to it)
    const target = await mountScheduler("resuming", "shutting-down");
    const [start, cancel] = buttons(target);
    expect(start.disabled).toBe(true);
    expect(hasIcon(cancel, "x")).toBe(true);
    expect(cancel.disabled).toBe(false);
  });
});

describe("TestSchedulerTooltips", () => {
  it("keeps the tooltip on the icon-only buttons only", async () => {
    // A button that shows its own name as its label needs no tooltip repeating
    // it; an icon-only button has no other place for its name, so its tooltip
    // is the name.
    const running = await mountScheduler("running", null);
    const [kill, schedulerStop] = buttons(running);
    expect(hasTooltip(kill)).toBe(false);
    expect(hasTooltip(schedulerStop)).toBe(true);

    const idle = await mountScheduler("idle", null);
    expect(hasTooltip(buttons(idle)[0])).toBe(false);

    const stopping = await mountScheduler("scheduler-stopping", null);
    const [killStopping, continueRunning] = buttons(stopping);
    expect(hasTooltip(killStopping)).toBe(false);
    expect(hasTooltip(continueRunning)).toBe(true);
  });
});

describe("TestSchedulerRestartTopic", () => {
  it("reads the phase from the Restart topic without a phase prop", async () => {
    const target = await mountScheduler("scheduler-stopping");
    const ws = FakeWebSocket.last!;
    ws.serverOpen();

    // no restart in progress: the plain stop
    expect(labels(target)).toEqual([t.Scheduler.Kill(), ""]);

    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: { phase: "stopping" } }));
    await flushEffects();
    expect(labels(target)).toEqual([t.Scheduler.KillKeepResume(), ""]);
    expect(hasIcon(buttons(target)[1], "x")).toBe(true);

    // the queue was processed: the topic holds 'done' until it is cleared
    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: { phase: "done" } }));
    await flushEffects();
    expect(labels(target)).toEqual([t.Scheduler.Kill(), ""]);
  });

  it("freezes the cancel of a parked worker on the shutting-down phase", async () => {
    const target = await mountScheduler("restarting");
    const ws = FakeWebSocket.last!;
    ws.serverOpen();

    expect(buttons(target)[1].disabled).toBe(false);

    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: { phase: "shutting-down" } }));
    await flushEffects();
    expect(buttons(target)[1].disabled).toBe(true);
  });
});
