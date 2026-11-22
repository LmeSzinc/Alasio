// Electron environment detection for the embedded web app.
//
// The webapp loads the frontend in an iframe with `?embedded=electron` and
// overlays its window controls (hide/minimize/maximize/close) at the top
// right of the page. The width the header has to keep clear for them is
// resolved by `$lib/window/frame.svelte`.
//
// There are two independent concepts:
// - `isElectronSession`: the ?embedded=electron URL query injected by the
//   webapp iframe. UI-only: it can be tampered with (e.g. by a remote
//   visitor) and only affects header layout avoidance.
// - `isElectron`: the authoritative embedded detection, driven by real
//   `alasio:*` postMessage traffic from the host. Remote browser sessions
//   never receive such messages and stay in per-client mode.
//
// Uplinks to the host live here too: `reportRoute` tells it which page to
// reopen after a window reload (see the webapp's embeddedRoute module).
//
// NOTE: useLocalStorage is not reused here because its $effect runes cannot
// be created at module top level (svelte 5 throws effect_orphan outside
// component initialization). Persistence is done manually instead.

const EMBEDDED_KEY = "alasio-embedded";

function readStored<T>(key: string, fallback: T): T {
  try {
    const item = localStorage.getItem(key);
    return item !== null ? (JSON.parse(item) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage unavailable (e.g. private mode); ignore
  }
}

// The ?embedded=electron query injected by the webapp iframe.
// Header-layout hint only; the authoritative embedded detection is the
// message-driven `isElectron` below.
export const isElectronSession = new URLSearchParams(location.search).get("embedded") === "electron";

// Authoritative embedded detection: set to true by the first validated
// `alasio:*` postMessage received from the parent window. The webapp host
// always sends lang/theme downlinks shortly after the iframe loads, so an
// embedded session flips this within the first frame. Remote browser
// sessions have no parent container and never trigger this.
// Exported as an object reference (read via `.value`): svelte 5 forbids
// exporting $state that is reassigned, so the mutable flag lives on a
// property instead of the exported binding itself.
export const isElectron = $state({ value: false });

// embedded: always mirrors the persisted value; an electron session forces
// true on every load (overriding whatever was stored).
const embedded = $state(isElectronSession || readStored(EMBEDDED_KEY, false));
if (isElectronSession) {
  writeStored(EMBEDDED_KEY, true);
}

// A real host presence is proven by `alasio:*` messages from the parent
// window; only those count, so a spoofed URL query cannot enable the
// host-sync behavior.
window.addEventListener("message", (event: MessageEvent) => {
  if (event.source !== window.parent) return;
  const data = event.data;
  if (
    data &&
    typeof data === "object" &&
    (data.type === "alasio:lang" || data.type === "alasio:theme" || data.type === "alasio:dpi-scaling")
  ) {
    isElectron.value = true;
  }
});

export const electronEnv = {
  get embedded() {
    return embedded;
  },
};

/**
 * Report the route the page is on to the host (the webapp window).
 *
 * The host reopens it after the window is reloaded: a reload remounts the
 * iframe at its URL, and without the route the app root opens the first
 * configuration instead of the page the user was on. The host rebuilds the
 * rest of the mount URL (theme, language, window controls) itself, so only
 * the path is sent.
 *
 * Args:
 *     route (str): Path of the current page, query and hash included
 */
export function reportRoute(route: string) {
  // A remote browser has no host: posting goes to its own window. The query
  // covers the first frames (no downlink arrived yet), `isElectron` covers the
  // rest of the session -- the report is then sent again whenever a downlink
  // proves the host is there.
  if (!isElectronSession && !isElectron.value) return;
  window.parent.postMessage({ type: "alasio:route", route }, "*");
}
