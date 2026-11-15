// Host-synced theme state for embedded sessions.
//
// The webapp host (main process AppState) is the single source of truth
// for the theme: it sends `alasio:theme` downlinks with the concrete
// display theme ('light' | 'dark'), and accepts `alasio:theme` uplinks
// with the config value ('system' | 'light' | 'dark') when the user
// changes the preference in the frontend.
//
// The preference is applied through mode-watcher's setMode, which also
// persists it (mirroring the language cookie: the frontend storage is only a
// per-client cache of the host-driven value). What is stored is the config
// value, not the display value: a 'system' preference has to keep following
// the os instead of being pinned to the mode it currently resolves to.
// Remote browser sessions have no parent container and keep the per-client
// mode-watcher behavior.
import { setMode } from "mode-watcher";
import { browser } from "$app/environment";
import { isElectron } from "$lib/use/useElectronEnv.svelte";

export type ConfigTheme = "system" | "light" | "dark";
export type DisplayTheme = "light" | "dark";

/**
 * Report the user-chosen config theme to the host (webapp main process).
 * The host derives the display theme and sends it back through the
 * downlink. Remote browser sessions never report.
 */
export function reportTheme(configTheme: ConfigTheme) {
  if (browser && isElectron.value) {
    window.parent.postMessage({ type: "alasio:theme", theme: configTheme }, "*");
  }
}

// Listen for the host-driven theme downlink. The host sends the concrete
// display theme (for painting) together with its config value (for the
// preference). The config value is what mode-watcher has to hold: storing the
// concrete value would flatten a 'system' preference into the mode it happens
// to resolve to right now, which both misreports the selection in the theme
// toggle and stops the page from following the os afterwards. mode-watcher
// resolves 'system' from the os itself, and the window's themeSource makes
// that the same answer the host derived.
if (browser) {
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window.parent) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "alasio:theme" && (data.theme === "light" || data.theme === "dark")) {
      const config = data.configTheme;
      const validConfig = config === "system" || config === "light" || config === "dark";
      setMode(validConfig ? config : data.theme);
    }
  });
}
