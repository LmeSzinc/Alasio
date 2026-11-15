/**
 * Tests for the host-driven theme downlink.
 *
 * The host sends the concrete display theme (for painting) together with its
 * config value. mode-watcher has to hold the config value: a 'system'
 * preference that is flattened into the mode it currently resolves to stops
 * following the os, and the theme toggle shows the wrong selection.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const { setMode } = vi.hoisted(() => ({ setMode: vi.fn() }));

vi.mock("mode-watcher", () => ({ setMode }));
vi.mock("$lib/use/useElectronEnv.svelte", () => ({ isElectron: { value: true } }));

/**
 * Deliver a downlink message the way the host posts it.
 *
 * Args:
 *     data (object): Message payload
 */
async function downlink(data: Record<string, unknown>) {
  window.dispatchEvent(new MessageEvent("message", { data, source: window.parent }));
  // let the module-level listener run
  await Promise.resolve();
}

beforeEach(async () => {
  // the listener is registered once, at module evaluation
  vi.resetModules();
  setMode.mockClear();
  await import("./state.svelte");
});

describe("TestThemeDownlink", () => {
  it("stores the config value, so 'system' is not flattened", async () => {
    // the host follows the os and currently resolves to dark
    await downlink({ type: "alasio:theme", theme: "dark", configTheme: "system" });

    // 'system' must survive: the page has to keep following the os
    expect(setMode).toHaveBeenCalledWith("system");
  });

  it("stores a concrete config value as is", async () => {
    await downlink({ type: "alasio:theme", theme: "dark", configTheme: "dark" });

    expect(setMode).toHaveBeenCalledWith("dark");
  });

  it("does not store the display value of a mismatched pair", async () => {
    // a light os with the user's 'dark' preference: the display value is the
    // resolved one, the config value is the preference
    await downlink({ type: "alasio:theme", theme: "dark", configTheme: "dark" });

    expect(setMode).not.toHaveBeenCalledWith("light");
  });

  it("falls back to the display value without a config value", async () => {
    await downlink({ type: "alasio:theme", theme: "light" });

    expect(setMode).toHaveBeenCalledWith("light");
  });

  it("ignores an invalid config value", async () => {
    await downlink({ type: "alasio:theme", theme: "dark", configTheme: "sepia" });

    expect(setMode).toHaveBeenCalledWith("dark");
  });

  it("ignores a message that is not a theme downlink", async () => {
    await downlink({ type: "alasio:theme", theme: "sepia", configTheme: "system" });
    await downlink({ type: "alasio:lang", lang: "ja-JP" });

    expect(setMode).not.toHaveBeenCalled();
  });
});
