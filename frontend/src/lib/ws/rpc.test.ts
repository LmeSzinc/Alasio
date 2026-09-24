import { toast } from "svelte-sonner";
import { effect_root } from "svelte/internal/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { goto } from "$app/navigation";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { websocketClient } from "./client.svelte";
import type { RequestEvent } from "./event";
import { routeState } from "./route-state.svelte";
import { type Rpc, type RpcCallbacks, type RpcContext, createResilientRpc, createRpc } from "./rpc.svelte";

// The rpc module shows error toasts through svelte-sonner; replace the
// module with spies so tests can assert the calls.
vi.mock("svelte-sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.stubGlobal("WebSocket", FakeWebSocket);

/** A minimal in-memory RpcContext (no real websocket involved). */
class FakeContext implements RpcContext {
  sent: RequestEvent[] = [];
  #callbacks = new Map<string, RpcCallbacks>();

  sendRaw(payload: RequestEvent): void {
    this.sent.push(payload);
  }
  registerRpcCall(id: string, callbacks: RpcCallbacks): void {
    this.#callbacks.set(id, callbacks);
  }
  unregisterRpcCall(id: string): void {
    this.#callbacks.delete(id);
  }
  hasRpcCall(id: string): boolean {
    return this.#callbacks.has(id);
  }
  callbacks(id: string): RpcCallbacks | undefined {
    return this.#callbacks.get(id);
  }
}

describe("TestCreateRpc", () => {
  let context: FakeContext;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    context = new FakeContext();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends the exact request payload structure with a unique id", () => {
    const rpc = createRpc("ConnState", context);
    rpc.call("set_lang", { lang: "zh-CN" });
    rpc.call("set_config", { name: "a" });

    expect(context.sent).toHaveLength(2);
    const [first, second] = context.sent;
    expect(first).toEqual({ t: "ConnState", o: "rpc", f: "set_lang", v: { lang: "zh-CN" }, i: expect.any(String) });
    expect(second).toEqual({ t: "ConnState", o: "rpc", f: "set_config", v: { name: "a" }, i: expect.any(String) });
    expect(first.i).not.toBe(second.i);
  });

  it("defaults the args payload to an empty object", () => {
    const rpc = createRpc("ConnState", context);
    rpc.call("set_lang");
    expect(context.sent[0]).toEqual({ t: "ConnState", o: "rpc", f: "set_lang", v: {}, i: expect.any(String) });
  });

  it("shows pending only after the default delay of 300ms", () => {
    const rpc = createRpc("ConnState", context);
    rpc.call("set_lang");

    vi.advanceTimersByTime(299);
    expect(rpc.isPending).toBe(false);
    vi.advanceTimersByTime(1);
    expect(rpc.isPending).toBe(true);
  });

  it("respects a custom pending delay", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 10 });
    rpc.call("set_lang");

    vi.advanceTimersByTime(9);
    expect(rpc.isPending).toBe(false);
    vi.advanceTimersByTime(1);
    expect(rpc.isPending).toBe(true);
  });

  it("resolves on success and cleans up its callbacks", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    const onSuccess = vi.fn();
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onSuccess, onError });
    const id = context.sent[0].i!;

    context.callbacks(id)!.onSuccess(id);
    expect(rpc.successMsg).toBe(id);
    expect(rpc.isPending).toBe(false);
    expect(rpc.errorMsg).toBeNull();
    expect(onSuccess).toHaveBeenCalledWith(id);
    expect(onError).not.toHaveBeenCalled();
    expect(context.hasRpcCall(id)).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("closes the bound dialog on success", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    rpc.open();
    expect(rpc.isOpen).toBe(true);

    rpc.call("set_lang");
    const id = context.sent[0].i!;
    context.callbacks(id)!.onSuccess(id);
    expect(rpc.isOpen).toBe(false);
  });

  it("resolves on error, shows a toast and cleans up", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    const onSuccess = vi.fn();
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onSuccess, onError });
    const id = context.sent[0].i!;

    context.callbacks(id)!.onError("boom");
    expect(rpc.errorMsg).toBe("boom");
    expect(rpc.isPending).toBe(false);
    expect(rpc.successMsg).toBeNull();
    expect(onError).toHaveBeenCalledWith("boom");
    expect(onSuccess).not.toHaveBeenCalled();
    expect(context.hasRpcCall(id)).toBe(false);
    expect(toast.error).toHaveBeenCalledWith('RPC call error on topic="ConnState", func="set_lang"', {
      description: "boom",
    });
  });

  it("times out after the default 5000ms", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onError });
    const id = context.sent[0].i!;

    vi.advanceTimersByTime(4999);
    expect(context.hasRpcCall(id)).toBe(true);
    expect(rpc.errorMsg).toBeNull();

    vi.advanceTimersByTime(1);
    expect(rpc.errorMsg).toBe("RPC call timeout");
    expect(onError).toHaveBeenCalledWith("RPC call timeout");
    expect(context.hasRpcCall(id)).toBe(false);
    expect(toast.error).toHaveBeenCalledWith('RPC call timeout on topic="ConnState", func="set_lang"', {
      description: "RPC call timeout",
    });
  });

  it("fires the timeout only once", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0, timeout: 100 });
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onError });

    vi.advanceTimersByTime(100);
    vi.advanceTimersByTime(1000);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it("drops a late server response after the timeout fired", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0, timeout: 100 });
    rpc.call("set_lang");
    const id = context.sent[0].i!;

    vi.advanceTimersByTime(100);
    // The callback was unregistered by the timeout; a late response is dropped.
    expect(context.callbacks(id)).toBeUndefined();
  });

  it("resets all state and clears timers", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    rpc.call("set_lang");
    vi.advanceTimersByTime(0);
    expect(rpc.isPending).toBe(true);

    rpc.reset();
    expect(rpc.isPending).toBe(false);
    expect(rpc.errorMsg).toBeNull();
    expect(rpc.successMsg).toBeNull();

    // After reset the timeout timer is gone: advancing past TIMEOUT
    // must not fire the timeout callback again.
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onError });
    const id = context.sent[1].i!;
    context.callbacks(id)!.onSuccess(id);
    vi.advanceTimersByTime(10000);
    expect(onError).not.toHaveBeenCalled();
  });

  it("open resets the state and shows the dialog", () => {
    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    rpc.call("set_lang");
    vi.advanceTimersByTime(0);
    expect(rpc.isPending).toBe(true);

    rpc.open();
    expect(rpc.isPending).toBe(false);
    expect(rpc.errorMsg).toBeNull();
    expect(rpc.isOpen).toBe(true);
  });
});

describe("TestElectronRenewalRetry", () => {
  let context: FakeContext;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    context = new FakeContext();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries exactly once after a successful renewal", async () => {
    // Failure trigger: ElectronOnlyError → renew → retry
    // the original call exactly once; a second failure is not retried.
    const { renewalCoordinator } = await import("./client.svelte");
    const renewSpy = vi.spyOn(renewalCoordinator, "renew").mockResolvedValue(true);

    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    const onError = vi.fn();
    const onSuccess = vi.fn();
    rpc.call("set_lang", { lang: "zh-CN" }, { onSuccess, onError });
    const firstId = context.sent[0].i!;

    // the server rejects the restricted rpc with an electron error
    context.callbacks(firstId)!.onError("ElectronOnlyError: Electron token required");
    expect(onError).toHaveBeenCalledTimes(1);
    expect(renewSpy).toHaveBeenCalledTimes(1);

    // the renewal resolves → the original call is retried with a fresh id
    await vi.advanceTimersByTimeAsync(0);
    expect(context.sent).toHaveLength(2);
    const retry = context.sent[1];
    expect(retry).toEqual({ t: "ConnState", o: "rpc", f: "set_lang", v: { lang: "zh-CN" }, i: expect.any(String) });
    expect(retry.i).not.toBe(firstId);

    // the retry succeeds: onSuccess fires for the retried call
    context.callbacks(retry.i!)!.onSuccess(retry.i!);
    expect(onSuccess).toHaveBeenCalledTimes(1);
    renewSpy.mockRestore();
  });

  it("does not retry a second ElectronOnlyError", async () => {
    const { renewalCoordinator } = await import("./client.svelte");
    const renewSpy = vi.spyOn(renewalCoordinator, "renew").mockResolvedValue(true);

    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    const onError = vi.fn();
    rpc.call("set_lang", {}, { onError });
    const firstId = context.sent[0].i!;

    context.callbacks(firstId)!.onError("ElectronOnlyError: Electron token required");
    await vi.advanceTimersByTimeAsync(0);
    expect(context.sent).toHaveLength(2);

    // second failure: no further renewal, no third attempt
    const retryId = context.sent[1].i!;
    context.callbacks(retryId)!.onError("ElectronOnlyError: Electron token required");
    await vi.advanceTimersByTimeAsync(0);
    expect(context.sent).toHaveLength(2);
    expect(renewSpy).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledTimes(2);
    renewSpy.mockRestore();
  });

  it("does not retry when the renewal fails", async () => {
    const { renewalCoordinator } = await import("./client.svelte");
    const renewSpy = vi.spyOn(renewalCoordinator, "renew").mockResolvedValue(false);

    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    rpc.call("set_lang");
    const id = context.sent[0].i!;

    context.callbacks(id)!.onError("ElectronOnlyError: Electron token required");
    await vi.advanceTimersByTimeAsync(0);
    // no retry after a failed renewal
    expect(context.sent).toHaveLength(1);
    expect(renewSpy).toHaveBeenCalledTimes(1);
    renewSpy.mockRestore();
  });

  it("does not renew on a non-electron error", async () => {
    const { renewalCoordinator } = await import("./client.svelte");
    const renewSpy = vi.spyOn(renewalCoordinator, "renew").mockResolvedValue(true);

    const rpc = createRpc("ConnState", context, { pendingDelay: 0 });
    rpc.call("set_lang");
    const id = context.sent[0].i!;

    context.callbacks(id)!.onError("RpcValueError: bad input");
    await vi.advanceTimersByTimeAsync(0);
    expect(context.sent).toHaveLength(1);
    expect(renewSpy).not.toHaveBeenCalled();
    renewSpy.mockRestore();
  });
});

describe("TestCreateResilientRpc", () => {
  let stopEffect: () => void;
  let rpc: Rpc;

  /** Connects the singleton websocketClient and returns the socket. */
  function connectClient(): FakeWebSocket {
    websocketClient.connect();
    const ws = FakeWebSocket.last!;
    ws.serverOpen();
    return ws;
  }

  /** Creates a resilient rpc inside an effect root so $effect works outside components. */
  async function makeResilientRpc(topic: string): Promise<void> {
    stopEffect = effect_root(() => {
      rpc = createResilientRpc(topic, websocketClient, { pendingDelay: 0, timeout: 5000 });
    });
    await flushEffects();
  }

  /** Returns the last request payload sent by the client. */
  function lastPayload(ws: FakeWebSocket): RequestEvent {
    const raw = ws.sent[ws.sent.length - 1];
    const text = typeof raw === "string" ? raw : new TextDecoder().decode(raw);
    return JSON.parse(text) as RequestEvent;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    // Close any connection left over from a previous test; the pending
    // reconnect timer is cleared by the next successful open.
    FakeWebSocket.instances.forEach((ws) => ws.serverClose(1006));
    FakeWebSocket.reset();
    // The singleton client persists across tests; drop subscription and
    // topic state so each test starts clean. disconnect() tears the
    // session down as a whole — unsubAll() would queue an unsub per topic
    // and open a fresh connection (polluting the instance list read by
    // the assertions below).
    websocketClient.disconnect();
    for (const key of Object.keys(websocketClient.topics)) {
      delete websocketClient.topics[key];
    }
    vi.clearAllMocks();
    // The singleton's session probe ('GET /api/auth/renew' via fetch) is
    // answered locally: a refused session, so a 4001 still ends the session
    // in the test that covers it, and no test ever reaches the network.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 401 })),
    );
    // The singleton ws client connects through the public-route guard.
    routeState.public = false;
  });

  afterEach(async () => {
    stopEffect?.();
    await flushEffects();
    vi.useRealTimers();
  });

  it("replays the last call after a reconnect once the topic is ready", async () => {
    const ws0 = connectClient();
    await makeResilientRpc("ConnState");
    const onSuccess = vi.fn();

    rpc.call("set_lang", { lang: "en-US" }, { onSuccess });
    expect(ws0.sent).toHaveLength(1);
    const first = lastPayload(ws0);

    // Server answers the first call.
    ws0.serverMessage(JSON.stringify({ t: "ConnState", i: first.i }));
    expect(onSuccess).toHaveBeenCalledTimes(1);

    // Connection drops and reopens: connectionGeneration increments and
    // the default subscription is marked ready again by onopen.
    ws0.serverClose(1006);
    vi.advanceTimersByTime(1000);
    const ws1 = FakeWebSocket.last!;
    ws1.serverOpen();
    await flushEffects();

    // The last call is replayed with the same payload and callbacks. The
    // rpc id is regenerated per call, so it differs from the first one.
    expect(ws1.sent).toHaveLength(1);
    const replayed = lastPayload(ws1);
    expect(replayed).toEqual({ ...first, i: expect.any(String) });
    expect(replayed.i).not.toBe(first.i);

    // The replayed call's callbacks are wired to the new id.
    ws1.serverMessage(JSON.stringify({ t: "ConnState", i: replayed.i }));
    expect(onSuccess).toHaveBeenCalledTimes(2);

    // Replay happens exactly once per reconnection.
    await flushEffects();
    expect(ws1.sent).toHaveLength(1);
  });

  it("does not replay while the previous call is still pending", async () => {
    const ws0 = connectClient();
    await makeResilientRpc("ConnState");

    rpc.call("set_lang", { lang: "en-US" });
    vi.advanceTimersByTime(0);
    expect(rpc.isPending).toBe(true);

    ws0.serverClose(1006);
    vi.advanceTimersByTime(1000);
    FakeWebSocket.last!.serverOpen();
    await flushEffects();

    expect(FakeWebSocket.last!.sent).toHaveLength(0);
    expect(rpc.isPending).toBe(true);
  });

  it("does not replay before the topic is ready again", async () => {
    const ws0 = connectClient();
    await makeResilientRpc("ConnState");

    rpc.call("set_lang", { lang: "en-US" });
    const first = lastPayload(ws0);
    ws0.serverMessage(JSON.stringify({ t: "ConnState", i: first.i }));

    ws0.serverClose(1006);
    vi.advanceTimersByTime(1000);
    const ws1 = FakeWebSocket.last!;
    ws1.serverOpen();
    // Simulate a topic that has not received its full snapshot yet.
    delete websocketClient.topicReady["ConnState"];
    await flushEffects();

    expect(ws1.sent).toHaveLength(0);

    // Even when the topic becomes ready later, the generation snapshot
    // was already advanced, so the call is not replayed twice.
    websocketClient.topicReady["ConnState"] = true;
    await flushEffects();
    expect(ws1.sent).toHaveLength(0);
  });

  it("does not revive the connection after an auth failure", async () => {
    // A rejected session (4001) never gets a replay. The replay signal —
    // the default subscription marked ready on open — fires on every
    // 4001-refused connection, so a replay would keep the client in a
    // connect -> 4001 -> goto -> replay loop whose navigation never
    // completes, and its pending call would toast a timeout over the
    // login page.
    const ws0 = connectClient();
    await makeResilientRpc("ConnState");

    rpc.call("set_lang", { lang: "en-US" });

    ws0.serverClose(4001);
    await vi.advanceTimersByTimeAsync(0);
    await flushEffects();

    expect(goto).toHaveBeenCalledWith("/auth");
    // The rejected session raises the shared flag that stops every client
    // from connecting until the login page takes over.
    expect(routeState.public).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(1);

    // The dropped call stays silent: no error toast, no retry, and no
    // connection ever comes back over the rejected session.
    vi.advanceTimersByTime(60_000);
    expect(toast.error).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
