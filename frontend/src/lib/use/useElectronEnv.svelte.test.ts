/**
 * Tests for the embedded-session detection and the route report.
 *
 * The detection is read once per document load, so every case re-evaluates the
 * module behind vi.resetModules() after preparing the URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const EMBEDDED_KEY = "alasio-embedded";

/**
 * Import a fresh instance of the environment module.
 *
 * Every case starts from an origin the client never ran on: the persisted
 * embedded flag of the module is cleared, so `isElectronSession` is the only
 * thing deciding the session.
 *
 * Args:
 *     search (str): Query string the module sees in `location.search`
 *
 * Returns:
 *     Promise[object]: The freshly evaluated module
 */
async function loadEnv(search: string) {
  window.history.replaceState({}, "", `/${search}`);
  localStorage.removeItem(EMBEDDED_KEY);
  vi.resetModules();
  return await import("./useElectronEnv.svelte");
}

/**
 * Spy on the messages the page sends to its parent.
 *
 * jsdom has no parent frame, so `window.parent` is the page itself and the spy
 * stands in for the host side as well.
 */
function spyOnPost() {
  return vi.spyOn(window, "postMessage").mockImplementation(() => {});
}

/**
 * Dispatch a message the way the host posts one: from the parent window.
 *
 * Args:
 *     data: Message payload
 */
function postFromHost(data: unknown) {
  window.dispatchEvent(new MessageEvent("message", { data, source: window as unknown as MessageEventSource }));
}

beforeEach(() => {
  localStorage.removeItem(EMBEDDED_KEY);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TestIsElectronSession", () => {
  it("detects the query the host writes into the iframe URL", async () => {
    const env = await loadEnv("?embedded=electron&theme=dark");
    expect(env.isElectronSession).toBe(true);
    expect(env.electronEnv.embedded).toBe(true);
  });

  it("does not treat a plain browser visit as embedded", async () => {
    const env = await loadEnv("");
    expect(env.isElectronSession).toBe(false);
    expect(env.electronEnv.embedded).toBe(false);
  });

  it("compares the query value exactly", async () => {
    const env = await loadEnv("?embedded=web");
    expect(env.isElectronSession).toBe(false);
  });
});

describe("TestReportRoute", () => {
  it("reports the route to the host of an embedded session", async () => {
    const env = await loadEnv("?embedded=electron&theme=dark");
    const post = spyOnPost();

    env.reportRoute("/config/cfg_a/overview?x=1");

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith({ type: "alasio:route", route: "/config/cfg_a/overview?x=1" }, "*");
  });

  it("stays silent in a remote browser", async () => {
    // A remote visitor has no host: the message would go to its own window
    const env = await loadEnv("");
    const post = spyOnPost();

    env.reportRoute("/config/cfg_a/overview");

    expect(post).not.toHaveBeenCalled();
  });

  it("reports once a downlink proved the host is there", async () => {
    // The authoritative detection needs no query: a single alasio:* message
    // from the parent is proof enough
    const env = await loadEnv("");
    const post = spyOnPost();
    expect(env.isElectron.value).toBe(false);

    postFromHost({ type: "alasio:theme", theme: "dark" });
    expect(env.isElectron.value).toBe(true);

    env.reportRoute("/config/cfg_a/arg");

    expect(post).toHaveBeenCalledWith({ type: "alasio:route", route: "/config/cfg_a/arg" }, "*");
  });

  it("ignores a message that is not a host downlink", async () => {
    const env = await loadEnv("");
    postFromHost({ type: "other" });

    expect(env.isElectron.value).toBe(false);
  });
});
