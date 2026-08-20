// Host-synced theme state for embedded sessions.
//
// The webapp host (main process AppState) is the single source of truth
// for the theme: it sends `alasio:theme` downlinks with the concrete
// display theme ('light' | 'dark'), and accepts `alasio:theme` uplinks
// with the config value ('system' | 'light' | 'dark') when the user
// changes the preference in the frontend.
//
// The concrete display value is applied through mode-watcher's setMode,
// which also persists it (mirroring the language cookie: the frontend
// storage is only a per-client cache of the host-driven value). Remote
// browser sessions have no parent container and keep the per-client
// mode-watcher behavior.

import { browser } from "$app/environment";
import { setMode } from "mode-watcher";
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
// display theme whenever its config or system theme changes.
if (browser) {
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window.parent) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "alasio:theme" && (data.theme === "light" || data.theme === "dark")) {
      setMode(data.theme);
    }
  });
}
