/**
 * Tests for the restart tool row (RestartBackendTool.svelte)
 *
 * The row carries the graceful restart and the force restart; while the
 * backend waits for the running configs to stop, the graceful button becomes
 * the cancel one. The tests drive the phase through the prop the dev page
 * overrides, plus the prop-less mounts that cover the production path (the
 * phase comes from the Restart topic then).
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RestartTopicLike } from "$lib/components/aside/types";
import { t } from "$lib/i18n";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import RestartBackendTool from "./RestartBackendTool.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

type RestartPhase = NonNullable<RestartTopicLike["phase"]>;

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

/**
 * Mounts the tool row; an undefined phase reads the Restart topic like
 * production
 */
async function mountTool(phase?: RestartPhase | null) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const component = mount(RestartBackendTool, { target, props: { phase } });
  mounted.push(component);
  await flushEffects();
  return target;
}

/** Buttons of the row in render order: restart (top), force restart (bottom) */
function buttons(target: HTMLElement) {
  return Array.from(target.querySelectorAll("button"));
}

/** Labels of the buttons */
function labels(target: HTMLElement) {
  return buttons(target).map((button) => button.textContent?.trim() ?? "");
}

/** Request payloads of a rpc the last socket sent, in order */
function rpcCalls(func: string) {
  const ws = FakeWebSocket.last;
  if (!ws) return [];
  return ws.sent
    .map((raw) => JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw)))
    .filter((message) => message.o === "rpc" && message.f === func)
    .map((message) => message.t);
}

beforeEach(() => {
  // The mounted row subscribes the topics: fake timers keep a pending
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

describe("TestRestartBackendToolButtons", () => {
  it("offers the restart and the force restart without a restart in progress", async () => {
    const target = await mountTool(null);
    const [restart, force] = buttons(target);
    expect(labels(target)).toEqual([t.DevTool.RestartBackend(), t.DevTool.ForceRestartBackend()]);
    expect(restart.disabled).toBe(false);
    expect(force.disabled).toBe(false);
  });

  it("becomes the cancel button while the configs are still stopping", async () => {
    const target = await mountTool("stopping");
    const [cancel, force] = buttons(target);
    expect(labels(target)).toEqual([t.DevTool.CancelRestart(), t.DevTool.ForceRestartBackend()]);
    expect(cancel.disabled).toBe(false);
    expect(force.disabled).toBe(false);
    // the cancel button follows the force restart one: same variant classes
    // (border and background), only the icon and the label differ
    expect(cancel.className).toBe(force.className);
    // live buttons carry none of the disabled look
    expect(cancel.classList.contains("disabled:cursor-not-allowed")).toBe(false);
    expect(cancel.classList.contains("disabled:bg-card")).toBe(false);
    expect(force.classList.contains("disabled:cursor-not-allowed")).toBe(false);
    // the tooltip of the button explains what the click does; the help column
    // of the row keeps describing the tool itself
    expect(cancel.title).toBe(t.DevTool.CancelRestartHelp());
    expect(target.textContent).toContain(t.DevTool.RestartBackendHelp());
  });

  it("keeps the cancel button, disabled, past the point of no return", async () => {
    // 'shutting-down': every config stopped, the resume list is frozen and the
    // backend process is leaving -- the cancel has nothing left to abort and
    // the force restart would talk to a backend that is already gone, so both
    // are only the record of the phase
    const target = await mountTool("shutting-down");
    const [cancel, force] = buttons(target);
    expect(labels(target)).toEqual([t.DevTool.CancelRestart(), t.DevTool.ForceRestartBackend()]);
    expect(cancel.disabled).toBe(true);
    expect(force.disabled).toBe(true);
    expect(cancel.title).toBe(t.DevTool.CancelRestartTooLate());
  });

  it("keeps the disabled buttons in their normal colours with a disabled cursor", async () => {
    // Disabled must not grey the row out: the standard shadcn fade is merged
    // away and the buttons take the colour of the card they sit on. The
    // not-allowed cursor is what tells that they are dead, and it sits on the
    // button itself -- the shadcn disabled style drops the pointer events, so
    // they have to come back or the button would never be hovered
    const target = await mountTool("shutting-down");
    for (const button of buttons(target)) {
      expect(button.classList.contains("disabled:opacity-50")).toBe(false);
      expect(button.classList.contains("disabled:opacity-100")).toBe(true);
      expect(button.classList.contains("disabled:bg-card")).toBe(true);
      expect(button.classList.contains("disabled:hover:bg-card")).toBe(true);
      expect(button.classList.contains("disabled:cursor-not-allowed")).toBe(true);
      expect(button.classList.contains("disabled:pointer-events-auto")).toBe(true);
      // no wrapper around the button for it
      expect(button.parentElement!.classList.contains("cursor-not-allowed")).toBe(false);
    }
  });

  it("offers the plain restart button once the new backend resumes", async () => {
    // the new backend is up: the restart of the previous phase is over and a
    // new one is allowed
    for (const phase of ["resuming", "done"] as const) {
      const target = await mountTool(phase);
      const [restart, force] = buttons(target);
      expect(labels(target)).toEqual([t.DevTool.RestartBackend(), t.DevTool.ForceRestartBackend()]);
      expect(restart.disabled).toBe(false);
      expect(force.disabled).toBe(false);
      // the plain restart button of the idle row is never disabled, so it must
      // not render the disabled look either
      expect(restart.classList.contains("disabled:cursor-not-allowed")).toBe(false);
      expect(force.classList.contains("disabled:cursor-not-allowed")).toBe(false);
    }
  });
});

describe("TestRestartBackendToolCancel", () => {
  it("cancels the restart through the ConnState rpc", async () => {
    const target = await mountTool("stopping");
    const ws = FakeWebSocket.last!;
    ws.serverOpen();
    await flushEffects();

    buttons(target)[0].click();
    await flushEffects();

    expect(rpcCalls("cancel_restart")).toEqual(["ConnState"]);
    // the cancel calls the backend instead of opening the restart dialog
    // (the dialog is the confirmation of a restart, there is none to confirm)
    expect(document.body.textContent).not.toContain(t.DevTool.RestartBackendConfirmTitle());
  });

  it("reads the phase from the Restart topic without a phase prop", async () => {
    const target = await mountTool();
    const ws = FakeWebSocket.last!;
    ws.serverOpen();

    expect(labels(target)).toEqual([t.DevTool.RestartBackend(), t.DevTool.ForceRestartBackend()]);

    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: { phase: "stopping" } }));
    await flushEffects();
    expect(labels(target)).toEqual([t.DevTool.CancelRestart(), t.DevTool.ForceRestartBackend()]);
    expect(buttons(target)[0].disabled).toBe(false);

    // every config stopped: the same button, disabled with the force one
    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: { phase: "shutting-down" } }));
    await flushEffects();
    expect(labels(target)).toEqual([t.DevTool.CancelRestart(), t.DevTool.ForceRestartBackend()]);
    expect(buttons(target)[0].disabled).toBe(true);
    expect(buttons(target)[1].disabled).toBe(true);

    // cancelled: the backend cleared the topic, the plain button is back
    ws.serverMessage(JSON.stringify({ t: "Restart", o: "full", v: {} }));
    await flushEffects();
    expect(labels(target)).toEqual([t.DevTool.RestartBackend(), t.DevTool.ForceRestartBackend()]);
    expect(buttons(target)[0].disabled).toBe(false);
    expect(buttons(target)[1].disabled).toBe(false);
  });
});
