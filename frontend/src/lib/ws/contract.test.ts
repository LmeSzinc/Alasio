import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeWebSocket } from "$lib/test-utils/fake-websocket";
import { WS_CLOSE_AUTH_FAILED, WS_CLOSE_AUTH_FAILED_REASON, WebsocketManager } from "./client.svelte";
import type { RequestEvent, ResponseEvent } from "./event";
import { routeState } from "./route-state.svelte";

// The ws framework imports svelte-sonner for rpc toasts; the contract
// test only exercises the protocol layer.
vi.mock("svelte-sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.stubGlobal("WebSocket", FakeWebSocket);

/**
 * Shared message fixtures: the same file (tests/ws_fixtures/messages.json)
 * is decoded by the backend test suite with msgspec, and asserted here
 * against the frontend protocol semantics. It is the single source of
 * truth for the wire format between the two sides.
 */
const fixtures = JSON.parse(readFileSync((import.meta.env as Record<string, string>).WS_FIXTURES_PATH, "utf8")) as {
  request: { omit_defaults: RequestEvent[]; full: RequestEvent[] };
  response: { omit_defaults: ResponseEvent[]; full: ResponseEvent[] };
  close: { auth_failed: { code: number; reason: string; meaning: string } };
};

beforeEach(() => {
  // The connect guard skips connections while a public route is
  // mounted; these tests drive the connection machinery directly.
  routeState.public = false;
});

describe("TestRequestEventContract", () => {
  it("omit_defaults requests only carry the fields that differ from defaults", () => {
    // {t: "ConfigScan"} is a sub request: o/f/v/i all fall back to
    // defaults ('sub' / '' / {} / ''), matching omit_defaults=True on
    // the backend RequestEvent struct.
    expect(Object.keys(fixtures.request.omit_defaults[0]).sort()).toEqual(["t"]);
    expect(fixtures.request.omit_defaults[1]).toEqual({ t: "ConfigScan", o: "unsub" });
  });

  it("full requests carry every protocol field", () => {
    expect(Object.keys(fixtures.request.full[0]).sort()).toEqual(["o", "t"]);
    expect(fixtures.request.full[1]).toEqual({
      t: "ConfigScan",
      o: "rpc",
      f: "config_add",
      v: { name: "new_config", mod: "example_mod" },
      i: "rpc-001",
    });
  });

  it("client serialization matches the fixture bytes exactly", () => {
    const client = new WebsocketManager();
    for (const payload of [...fixtures.request.omit_defaults, ...fixtures.request.full]) {
      client.sendRaw(payload);
    }
    FakeWebSocket.last!.serverOpen();

    const encoded = FakeWebSocket.last!.sent.map((raw) =>
      typeof raw === "string" ? raw : new TextDecoder().decode(raw),
    );
    const expected = [...fixtures.request.omit_defaults, ...fixtures.request.full].map((payload) =>
      JSON.stringify(payload),
    );
    expect(encoded).toEqual(expected);
  });
});

describe("TestResponseEventContract", () => {
  it("an rpc response without v means success", () => {
    const success = fixtures.response.omit_defaults[3];
    expect(success).toEqual({ t: "ConnState", i: "rpc-001" });
    expect("v" in success).toBe(false);
  });

  it("an rpc response with a string v means failure", () => {
    const failure = fixtures.response.omit_defaults[4];
    expect(failure).toEqual({ t: "ConnState", i: "rpc-002", v: 'No such config: "foo"' });
    expect(typeof failure.v).toBe("string");
  });

  it("keys in data events is a path array", () => {
    const set = fixtures.response.omit_defaults[1];
    const del = fixtures.response.omit_defaults[2];
    expect(Array.isArray(set.k)).toBe(true);
    expect(set.k).toEqual(["lang"]);
    expect(del.k).toEqual(["nav_name"]);
  });

  it("applies data events to the topic state", () => {
    const client = new WebsocketManager();
    client.connect();
    FakeWebSocket.last!.serverOpen();

    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.omit_defaults[0]));
    expect(client.topics.ConnState).toEqual({
      lang: "en-US",
      config_name: "",
      mod_name: "",
      nav_name: "",
    });

    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.omit_defaults[1]));
    expect(client.topics.ConnState.lang).toBe("zh-CN");

    // Note: svelte 5.56's $state proxy keeps the deleted key visible to
    // the `in` operator (delete only unregisters the reactive source),
    // so the value is asserted instead of toHaveProperty.
    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.omit_defaults[2]));
    expect((client.topics.ConnState as Record<string, any>).nav_name).toBeUndefined();
  });

  it("applies full-form events including explicit empty keys", () => {
    const client = new WebsocketManager();
    client.connect();
    FakeWebSocket.last!.serverOpen();

    // add with k=[] replaces the whole topic.
    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.full[0]));
    expect(client.topics.ConnState).toEqual({});

    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.full[1]));
    expect(client.topics.ConnState).toEqual({ nav: { name: "overview" } });

    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.full[2]));
    expect(client.topics.ConnState).toEqual({ lang: "en-US" });
  });

  it("dispatches rpc responses to registered callbacks", () => {
    const client = new WebsocketManager();
    client.connect();
    FakeWebSocket.last!.serverOpen();
    const onSuccess = vi.fn();
    const onError = vi.fn();

    client.registerRpcCall("rpc-001", { onSuccess, onError });
    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.omit_defaults[3]));
    expect(onSuccess).toHaveBeenCalledWith("rpc-001");
    expect(onError).not.toHaveBeenCalled();

    client.registerRpcCall("rpc-002", { onSuccess, onError });
    FakeWebSocket.last!.serverMessage(JSON.stringify(fixtures.response.omit_defaults[4]));
    expect(onError).toHaveBeenCalledWith('No such config: "foo"');
  });
});

describe("TestCloseContract", () => {
  it("the auth-failure close signal matches the fixture", () => {
    // The one refusal the client ends a session on is the pair the backend
    // sends when the credentials are not accepted. It is pinned in the shared
    // fixture (asserted here and by the backend test suite), so renaming it on
    // either side breaks the other side's test instead of silently turning a
    // refused handshake into a navigation to the login page.
    expect(fixtures.close.auth_failed.code).toBe(WS_CLOSE_AUTH_FAILED);
    expect(fixtures.close.auth_failed.reason).toBe(WS_CLOSE_AUTH_FAILED_REASON);
  });
});

beforeEach(() => {
  FakeWebSocket.reset();
  vi.clearAllMocks();
});
