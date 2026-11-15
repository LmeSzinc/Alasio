/**
 * Unit tests for the display values of the host (AppState).
 *
 * The theme has two halves that have to agree: the display theme the renderer
 * paints itself with, and Chromium's own light/dark state (`themeSource`),
 * which drives `prefers-color-scheme` inside the renderer and the native UI
 * Electron renders. A 'system' config has to read the OS value, a concrete one
 * has to override it.
 *
 * electron is stubbed: the node test environment cannot start it, and the
 * points under test are the decisions AppState makes, not electron's behaviour.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const { nativeThemeStub, stubState } = vi.hoisted(() => {
  const state = {
    themeSource: "system",
    osDark: false,
    listeners: [] as Array<() => void>,
    calls: [] as string[],
  };

  const stub = {
    /** Electron reports the override when one is set, the OS value otherwise. */
    get shouldUseDarkColors() {
      state.calls.push("get shouldUseDarkColors");
      return state.themeSource === "system" ? state.osDark : state.themeSource === "dark";
    },
    get themeSource() {
      return state.themeSource;
    },
    /** Setting the same value is a no-op, any change emits 'updated' once. */
    set themeSource(value: string) {
      state.calls.push(`set themeSource=${value}`);
      if (state.themeSource === value) return;
      state.themeSource = value;
      for (const listener of state.listeners) listener();
    },
    on: (_event: string, listener: () => void) => {
      state.listeners.push(listener);
    },
    /** Report an OS-level change, the way electron does. */
    emitUpdated: () => {
      for (const listener of state.listeners) listener();
    },
  };

  return { nativeThemeStub: stub, stubState: state };
});

vi.mock("electron", () => ({
  app: {
    getLocale: () => "en-US",
    getPreferredSystemLanguages: () => ["en-US"],
    getPath: () => "",
    on: () => {},
    whenReady: () => Promise.resolve(),
  },
  nativeTheme: nativeThemeStub,
  BrowserWindow: class {},
  session: {},
}));

/**
 * A fresh AppState module over the stubbed electron.
 *
 * Args:
 *     osDark (bool): Whether the stubbed OS is in dark mode
 *     initialThemeSource (str): Value `themeSource` already holds, emulating a
 *         stale override left by a previous session
 *
 * Returns:
 *     Promise[object]: The freshly evaluated module, exposing `appState`
 */
async function loadAppState(osDark: boolean, initialThemeSource = "system") {
  stubState.themeSource = initialThemeSource;
  stubState.osDark = osDark;
  stubState.listeners.length = 0;
  stubState.calls.length = 0;
  vi.resetModules();
  return await import("./app-state");
}

beforeEach(() => {
  stubState.calls.length = 0;
});

describe("TestDeriveDisplayTheme", () => {
  it("follows the os while the config is 'system'", async () => {
    const { appState } = await loadAppState(true);
    appState.configTheme = "system";

    appState.init();

    expect(appState.displayTheme).toBe("dark");
    expect(nativeThemeStub.themeSource).toBe("system");
  });

  it("follows a light os while the config is 'system'", async () => {
    const { appState } = await loadAppState(false);
    appState.configTheme = "system";

    appState.init();

    expect(appState.displayTheme).toBe("light");
    expect(nativeThemeStub.themeSource).toBe("system");
  });

  it("overrides a light os when the config is dark", async () => {
    const { appState } = await loadAppState(false);
    appState.configTheme = "dark";

    appState.init();

    expect(appState.displayTheme).toBe("dark");
    // the part the renderer's prefers-color-scheme and the native ui follow
    expect(nativeThemeStub.themeSource).toBe("dark");
    expect(nativeThemeStub.shouldUseDarkColors).toBe(true);
  });

  it("overrides a dark os when the config is light", async () => {
    const { appState } = await loadAppState(true);
    appState.configTheme = "light";

    appState.init();

    expect(appState.displayTheme).toBe("light");
    expect(nativeThemeStub.themeSource).toBe("light");
    expect(nativeThemeStub.shouldUseDarkColors).toBe(false);
  });

  it("settles themeSource before reading the os value", async () => {
    // The ordering is what makes 'system' correct after an override: reading
    // shouldUseDarkColors first would answer for the previous override.
    const { appState } = await loadAppState(true, "light");
    appState.configTheme = "system";
    stubState.calls.length = 0;

    appState.init();

    const assigned = stubState.calls.indexOf("set themeSource=system");
    const read = stubState.calls.indexOf("get shouldUseDarkColors");
    expect(assigned).toBeGreaterThanOrEqual(0);
    expect(read).toBeGreaterThan(assigned);
    // the OS value is the one that ends up as the display theme
    expect(appState.displayTheme).toBe("dark");
  });
});

describe("TestSetTheme", () => {
  it("moves the display theme and the chromium state together", async () => {
    const { appState } = await loadAppState(false);
    appState.init();

    appState.setTheme("dark");

    expect(appState.configTheme).toBe("dark");
    expect(appState.displayTheme).toBe("dark");
    expect(nativeThemeStub.themeSource).toBe("dark");
  });

  it("returns to the os preference for 'system'", async () => {
    const { appState } = await loadAppState(false);
    appState.init();
    appState.setTheme("dark");
    expect(appState.displayTheme).toBe("dark");

    appState.setTheme("system");

    expect(appState.displayTheme).toBe("light");
    expect(nativeThemeStub.themeSource).toBe("system");
  });

  it("ignores an unknown value", async () => {
    const { appState } = await loadAppState(false);
    appState.init();

    appState.setTheme("sepia");

    expect(appState.configTheme).toBe("system");
    expect(appState.displayTheme).toBe("light");
  });

  it("notifies its listeners", async () => {
    const { appState } = await loadAppState(false);
    appState.init();
    const seen: string[] = [];
    appState.onChange(() => seen.push(appState.displayTheme));

    appState.setTheme("dark");

    expect(seen).toEqual(["dark"]);
  });
});

describe("TestSystemThemeListener", () => {
  it("re-derives when the os changes while the config is 'system'", async () => {
    const { appState } = await loadAppState(false);
    appState.configTheme = "system";
    appState.init();
    expect(appState.displayTheme).toBe("light");

    stubState.osDark = true;
    nativeThemeStub.emitUpdated();

    expect(appState.displayTheme).toBe("dark");
    expect(nativeThemeStub.themeSource).toBe("system");
  });

  it("ignores the os while a concrete value is configured", async () => {
    const { appState } = await loadAppState(false);
    appState.configTheme = "dark";
    appState.init();

    stubState.osDark = false;
    nativeThemeStub.emitUpdated();

    expect(appState.displayTheme).toBe("dark");
    expect(nativeThemeStub.themeSource).toBe("dark");
  });

  it("does not loop on its own assignment", async () => {
    // deriveDisplay assigns themeSource, which emits 'updated' on a change.
    // The listener re-derives only for 'system', and a same-value assignment
    // emits nothing, so the recursion stops after one extra pass.
    const { appState } = await loadAppState(true);
    appState.configTheme = "system";
    appState.init();

    stubState.calls.length = 0;
    nativeThemeStub.emitUpdated();

    // one derive pass: one assignment (a no-op, so no further event) and one read
    expect(stubState.calls.filter((c) => c.startsWith("set themeSource="))).toHaveLength(1);
    expect(stubState.calls.filter((c) => c === "get shouldUseDarkColors")).toHaveLength(1);
  });
});
