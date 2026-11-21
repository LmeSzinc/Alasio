// Host-synced dpi scaling state for embedded sessions.
//
// The webapp host (main process AppState) is the single source of truth
// for the dpi scaling: it sends `alasio:dpi-scaling` downlinks with the
// host value (true = follow the system DPI scaling, false = force scale
// factor 1), and accepts `alasio:dpi-scaling` uplinks when the user
// changes the preference in the frontend.
//
// Unlike language/theme there is a single value (no config/display
// split): the preference only affects the electron window through its
// startup command-line switch (force-device-scale-factor), so changes
// apply on the next launch. Remote browser sessions have no parent
// container and never sync.
import { browser } from "$app/env";
import { isElectron } from "$lib/use/useElectronEnv.svelte";

let dpiScaling = $state(true);

// === Helpers ===

/**
 * Apply the host value without reporting back. Used by the
 * `alasio:dpi-scaling` downlink: the host is the source of truth, so
 * host-driven changes must not loop back to it.
 */
function applyDpiScaling(value: boolean) {
  if (value === dpiScaling) return;
  dpiScaling = value;
}

/**
 * Report the user-chosen value to the host (webapp main process). The
 * host persists it into deploy.yaml through the stdin contract. Remote
 * browser sessions have no parent container and never report.
 */
function reportDpiScaling(value: boolean) {
  if (browser && isElectron.value) {
    window.parent.postMessage({ type: "alasio:dpi-scaling", dpiScaling: value }, "*");
  }
}

// === Public API ===

/**
 * Switch dpi scaling (user entry). Updates the local value and, in an
 * embedded session, reports it to the host. The host persists the value
 * and applies it through the electron startup parameters on the next
 * launch.
 */
export function setDpiScaling(value: boolean) {
  if (value === dpiScaling) return;
  dpiScaling = value;
  reportDpiScaling(value);
}

// Listen for the host-driven downlink. The host sends its current dpi
// scaling whenever it changes (and on the ready handshake).
if (browser) {
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window.parent) return;
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "alasio:dpi-scaling" && typeof data.dpiScaling === "boolean") {
      applyDpiScaling(data.dpiScaling);
    }
  });
}

// Export state for read-only access if needed
export const dpiState = {
  get value() {
    return dpiScaling;
  },
};
