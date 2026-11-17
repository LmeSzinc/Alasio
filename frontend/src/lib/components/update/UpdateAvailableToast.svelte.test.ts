/**
 * Tests for the trigger of the "update available" toast
 * (UpdateAvailableToast.svelte)
 *
 * The component watches the Update topic and hands the available updates to
 * the toast module. sonner is mocked, so the tests assert when a toast pops
 * and which updates it carries (the whole available set), not how it renders;
 * the component itself renders nothing.
 *
 * The last group covers the data prop the debug page mounts the trigger with:
 * the prop replaces the topic, so the preview page drives the production
 * filter, announcement set and toast with a faked topic.
 */
import { mount, unmount } from "svelte";
import { type ToastT, toast } from "svelte-sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UPDATE_STATE, UpdateInfoLike, UpdateTopicLike } from "$lib/components/aside/types";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "$lib/ws";
import UpdateAvailableToast from "./UpdateAvailableToast.svelte";
import type { UpdateToastData } from "./updateToast";

vi.mock("svelte-sonner", () => ({
  toast: { custom: vi.fn(), dismiss: vi.fn(), getActiveToasts: vi.fn(() => []) },
}));

vi.stubGlobal("WebSocket", FakeWebSocket);

// mount() is generic over the component's props/exports, so its return type
// cannot be spelled with ReturnType; the array only holds component handles
// for unmount.
let mounted: any[] = [];

// Versions of the mock topic: 40 hex characters are the sha1 of a packed
// commit, anything else is a tag
const SHA_A = "0beb992a4b31f0e2f8b2f3b7a1c2d3e4f5a6b7c8";
const SHA_B = "d3599d6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e";

/** One entry of the Update topic, as the backend reports it. */
function entry(state: UPDATE_STATE, latest = ""): UpdateInfoLike {
  return {
    state,
    current_version: "ab41c81d",
    latest_version: latest,
    checked_at: 1760000000,
    error: "",
  };
}

/** One entry of toast.getActiveToasts(), as much as the module reads of it. */
function liveToast(id: string | number): ToastT {
  return { id, type: "default" };
}

/** Mount the trigger of the (private) layout and accept its subscription. */
async function mountTrigger() {
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(UpdateAvailableToast, { target }));
  await flushEffects();
  FakeWebSocket.last!.serverOpen();
  await flushEffects();
}

/**
 * Mount the trigger the way the debug page does: with the data of the topic
 * as a prop. The returned props object is reactive, so a test can replace the
 * data the way a button of the page does.
 */
async function mountPreview(update: UpdateTopicLike) {
  const props = $state<{ update?: UpdateTopicLike }>({ update });
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(UpdateAvailableToast, { target, props: props as { update?: UpdateTopicLike } }));
  await flushEffects();
  FakeWebSocket.last?.serverOpen();
  await flushEffects();
  return props;
}

/** Push a full snapshot of the Update topic, as the backend sends on subscribe. */
async function pushSnapshot(update: UpdateTopicLike) {
  FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "Update", o: "full", v: update }));
  await flushEffects();
}

/** Push the state of one mod, as the backend does when a check finishes. */
async function pushMod(mod: string, info: UpdateInfoLike) {
  FakeWebSocket.last!.serverMessage(JSON.stringify({ t: "Update", o: "set", k: [mod], v: info }));
  await flushEffects();
}

/** The updates carried by every toast created so far, in creation order. */
function announcedSets(): UpdateToastData[][] {
  return vi
    .mocked(toast.custom)
    .mock.calls.map(([, options]) => (options!.componentProps as { updates: UpdateToastData[] }).updates);
}

/** The id of the n-th toast created so far, as the toast module assigned it. */
function toastId(index: number): string | number {
  return vi.mocked(toast.custom).mock.calls[index][1]!.id!;
}

beforeEach(() => {
  // the trigger subscribes on mount: fake timers keep a pending reconnect of
  // the shared websocket client from firing into the next test
  vi.useFakeTimers();
  FakeWebSocket.reset();
  vi.clearAllMocks();
  vi.mocked(toast.getActiveToasts).mockReturnValue([]);
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

describe("TestUpdateAvailableToast", () => {
  it("pops the toast when a mod becomes available", async () => {
    await mountTrigger();

    await pushSnapshot({ alasio: entry("available", SHA_A) });

    expect(announcedSets()).toEqual([[{ mod: "alasio", latest: SHA_A }]]);
  });

  it("stays quiet when the same pair is pushed again", async () => {
    await mountTrigger();
    await pushSnapshot({ alasio: entry("available", SHA_A) });

    // a rebuilt snapshot and a per-mod state push both carry the same pair
    await pushSnapshot({ alasio: entry("available", SHA_A) });
    await pushMod("alasio", entry("available", SHA_A));

    expect(announcedSets()).toHaveLength(1);
  });

  it("stays quiet for the same pair after the user closed the toast", async () => {
    await mountTrigger();
    await pushSnapshot({ alasio: entry("available", SHA_A) });
    // the user closes the toast: it leaves sonner, the announcement stays
    vi.mocked(toast.getActiveToasts).mockReturnValue([]);

    // the periodic check of the backend reports the same pair again (a new
    // UpdateInfo of the same values, every 5 minutes in production): closing
    // the toast only closes that message, the pair is not announced twice
    await pushMod("alasio", entry("available", SHA_A));

    expect(announcedSets()).toHaveLength(1);
  });

  it("aggregates a second available mod into one toast, replacing the one on screen", async () => {
    await mountTrigger();
    await pushSnapshot({ alasio: entry("available", SHA_A) });
    // the first toast is still on screen when the second mod becomes available
    vi.mocked(toast.getActiveToasts).mockReturnValue([liveToast(toastId(0))]);

    await pushSnapshot({ alasio: entry("available", SHA_A), example_mod: entry("available", SHA_B) });

    expect(announcedSets()).toEqual([
      [{ mod: "alasio", latest: SHA_A }],
      [
        { mod: "alasio", latest: SHA_A },
        { mod: "example_mod", latest: SHA_B },
      ],
    ]);
    // the replacement goes through the toast module: the previous toast is
    // dismissed under its own id instead of two toasts stacking up
    expect(toast.dismiss).toHaveBeenCalledTimes(1);
    expect(toast.dismiss).toHaveBeenCalledWith(toastId(0));
  });

  it("pops the toast again for a newer version, after the user closed it", async () => {
    await mountTrigger();
    await pushSnapshot({ alasio: entry("available", SHA_A) });
    vi.mocked(toast.getActiveToasts).mockReturnValue([]);

    await pushMod("alasio", entry("available", SHA_B));

    expect(announcedSets()).toEqual([[{ mod: "alasio", latest: SHA_A }], [{ mod: "alasio", latest: SHA_B }]]);
  });

  it("stays quiet without an available pair carrying a version", async () => {
    await mountTrigger();

    await pushSnapshot({
      uptodate_mod: entry("uptodate", SHA_A),
      unknown_version_mod: entry("available", ""),
      checking_mod: entry("checking", SHA_B),
    });

    expect(toast.custom).not.toHaveBeenCalled();
  });

  it("announces the pairs not announced before a reconnect", async () => {
    await mountTrigger();
    await pushSnapshot({ alasio: entry("available", SHA_A) });

    // the connection drops and the client reconnects to the same backend
    FakeWebSocket.last!.serverClose(1006);
    vi.advanceTimersByTime(2000);
    await flushEffects();
    expect(FakeWebSocket.instances).toHaveLength(2);
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    // the full snapshot of the new session repeats the announced pair and
    // carries a new one: only a toast with the new pair is shown
    await pushSnapshot({ alasio: entry("available", SHA_A), example_mod: entry("available", SHA_B) });

    expect(announcedSets()).toEqual([
      [{ mod: "alasio", latest: SHA_A }],
      [
        { mod: "alasio", latest: SHA_A },
        { mod: "example_mod", latest: SHA_B },
      ],
    ]);
  });
});

describe("TestUpdateAvailableToastWhenPreviewed", () => {
  it("announces the data of the prop instead of the topic", async () => {
    await mountPreview({ alasio: entry("available", SHA_A) });

    expect(announcedSets()).toEqual([[{ mod: "alasio", latest: SHA_A }]]);

    // the topic of the page is not an input of the preview mount: a real
    // push must not pop another toast over the previewed one
    await pushSnapshot({ example_mod: entry("available", SHA_B) });

    expect(announcedSets()).toHaveLength(1);
  });

  it("announces the next pair when the previewed data changes", async () => {
    const props = await mountPreview({ alasio: entry("available", SHA_A) });
    expect(announcedSets()).toHaveLength(1);

    // what a scenario button of the debug page does: a new topic value
    props.update = { alasio: entry("available", SHA_A), example_mod: entry("available", SHA_B) };

    await flushEffects();
    expect(announcedSets()).toEqual([
      [{ mod: "alasio", latest: SHA_A }],
      [
        { mod: "alasio", latest: SHA_A },
        { mod: "example_mod", latest: SHA_B },
      ],
    ]);
  });
});
