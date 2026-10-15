/**
 * Tests for the control row of the update flow (UpdateControl.svelte)
 *
 * The row derives the state of the updates from the mods and their update
 * entries, opens the confirmation of the single update and calls the rpcs of
 * the Update topic. The tests drive it through the props (mods / update), no
 * topic data is involved; the rpcs are asserted on the fake socket.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UPDATE_STATE, UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
import { type ModOption } from "$lib/components/update/utils";
import { t } from "$lib/i18n";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import UpdateControl from "./UpdateControl.svelte";

vi.stubGlobal("WebSocket", FakeWebSocket);

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

const MODS: ModOption[] = [
  { value: "alasio", label: "alasio" },
  { value: "example_mod", label: "example_mod" },
];

/** One entry of the Update topic, as the backend reports it. */
function update(state: UPDATE_STATE, current = "", latest = "", error = ""): UpdateInfoLike {
  return {
    state,
    current_version: current,
    latest_version: latest,
    checked_at: Math.floor(Date.now() / 1000),
    error,
  };
}

type Props = {
  mods?: ModOption[];
  update?: UpdateTopicLike;
  onUpdate?: () => void;
  onCancel?: () => void;
};

async function mountControl(props: Props) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(UpdateControl, { target, props: { mods: MODS, ...props } }));
  await flushEffects();
  return target;
}

/** textContent with the whitespace of the template collapsed. */
function text(element: HTMLElement): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** The button of the given label inside a scope, or null. */
function findButton(scope: HTMLElement | Document, label: string): HTMLButtonElement | null {
  return [...scope.querySelectorAll("button")].find((b) => b.textContent?.trim() === label) ?? null;
}

function button(target: HTMLElement, label: string): HTMLButtonElement {
  const element = findButton(target, label);
  expect(element, `no "${label}" button`).not.toBeNull();
  return element!;
}

function dialog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-slot="alert-dialog-content"]');
}

/** Request payloads of a rpc the last socket sent, in order. */ function rpcCalls(func: string) {
  const ws = FakeWebSocket.last;
  if (!ws) return [];
  return ws.sent
    .map((raw) => JSON.parse(typeof raw === "string" ? raw : new TextDecoder().decode(raw)))
    .filter((message) => message.o === "rpc" && message.f === func)
    .map((message) => message.t);
}

beforeEach(() => {
  // the mounted row subscribes the Update topic: fake timers keep a pending
  // reconnect of the shared websocket client from firing into the next test
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.clearAllMocks();
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

describe("TestUpdateControl", () => {
  it("stands the buttons at the left, the state of the updates after them", async () => {
    const target = await mountControl({ update: { alasio: update("available"), example_mod: update("available") } });

    // [update] [state]: the update button then the state text
    const parts = [...target.children[0].children].map((child) => text(child as HTMLElement));
    expect(parts).toEqual([t.Update.ButtonUpdate(), t.Update.AvailableCount({ count: "2" })]);
    expect(button(target, t.Update.ButtonUpdate()).disabled).toBe(false);
    // no cancel button: nothing is in flight
    expect(findButton(target, t.Update.ButtonCancel())).toBeNull();
  });

  it("disables the update button without an available update", async () => {
    const target = await mountControl({ update: { alasio: update("uptodate") } });
    expect(button(target, t.Update.ButtonUpdate()).disabled).toBe(true);
  });

  it("explains why no update is offered instead of blaming the mods", async () => {
    // the backend runs without a live supervisor
    let target = await mountControl({ update: { alasio: update("no-supervisor") } });
    expect(text(target)).toContain(t.Update.UpdatesNoSupervisor());

    // the mod updates are off for this backend
    target = await mountControl({ update: { alasio: update("disabled") } });
    expect(text(target)).toContain(t.Update.UpdatesDisabled());

    // every managed mod is up to date
    target = await mountControl({ update: { alasio: update("uptodate") } });
    expect(text(target)).toContain(t.Update.NoAvailable());
  });

  it("shows the cancel button while the flow may still be cancelled", async () => {
    const target = await mountControl({ update: { alasio: update("downloading") } });
    expect(findButton(target, t.Update.ButtonCancel())).not.toBeNull();
  });

  it("applies the update and cancels the flow through the rpcs of the topic", async () => {
    const target = await mountControl({ update: { alasio: update("available") } });
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    // no confirmation dialog: reaching this row from the popup that announces
    // the update already is the confirmation
    button(target, t.Update.ButtonUpdate()).click();
    await flushEffects();
    expect(rpcCalls("update_apply")).toEqual(["Update"]);
    expect(dialog()).toBeNull();

    // the cancel button of a flow in flight calls the cancel rpc
    const cancelling = await mountControl({ update: { alasio: update("downloading") } });
    FakeWebSocket.last!.serverOpen();
    await flushEffects();
    button(cancelling, t.Update.ButtonCancel()).click();
    await flushEffects();
    expect(rpcCalls("cancel_apply")).toEqual(["Update"]);
  });

  it("lets a caller take the buttons over", async () => {
    const onUpdate = vi.fn();
    const onCancel = vi.fn();
    const target = await mountControl({
      update: { alasio: update("available"), example_mod: update("downloading") },
      onUpdate,
      onCancel,
    });

    button(target, t.Update.ButtonUpdate()).click();
    await flushEffects();
    expect(onUpdate).toHaveBeenCalledTimes(1);

    button(target, t.Update.ButtonCancel()).click();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
