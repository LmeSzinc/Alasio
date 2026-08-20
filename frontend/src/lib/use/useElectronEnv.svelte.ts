// Electron environment detection for the embedded web app.
//
// The webapp loads the frontend in an iframe with `?embedded=electron` and
// overlays its window controls (hide/minimize/maximize/close) at the top
// right of the page. The frontend can reserve space for them in its own
// header (see `electronEnv.shouldAvoid`).
//
// There are two independent concepts:
// - `isElectronSession`: the ?embedded=electron URL query injected by the
//   webapp iframe. UI-only: it can be tampered with (e.g. by a remote
//   visitor) and only affects header layout avoidance.
// - `isElectron`: the authoritative embedded detection, driven by real
//   `alasio:*` postMessage traffic from the host. Remote browser sessions
//   never receive such messages and stay in per-client mode.
//
// NOTE: useLocalStorage is not reused here because its $effect runes cannot
// be created at module top level (svelte 5 throws effect_orphan outside
// component initialization). Persistence is done manually instead.

export type WindowControlsAvoidMode = "auto" | "always" | "never";

// Window controls: hide + minimize + maximize + close, each 48px (w-12) wide
export const WINDOW_CONTROLS_WIDTH = 192;

const EMBEDDED_KEY = "alasio-embedded";
const AVOID_KEY = "alasio-window-controls-avoid";

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
  if (data && typeof data === "object" && (data.type === "alasio:lang" || data.type === "alasio:theme")) {
    isElectron.value = true;
  }
});

// Avoidance mode: always mirrors localStorage (single source of truth).
// An electron session resets it to the default once at startup through the
// setter (see below), so refresh restores the defaults while a later
// non-electron visit keeps the last persisted setting.
let avoidMode = $state<WindowControlsAvoidMode>(readStored(AVOID_KEY, "auto"));

export const electronEnv = {
  get embedded() {
    return embedded;
  },

  get avoidMode() {
    return avoidMode;
  },

  set avoidMode(mode: WindowControlsAvoidMode) {
    avoidMode = mode;
    writeStored(AVOID_KEY, mode);
  },

  /** Whether the header should reserve space for the electron window controls */
  get shouldAvoid() {
    return avoidMode === "always" || (avoidMode === "auto" && (embedded || isElectron.value));
  },
};

// Electron session: reset the avoidance mode to the default on every load
// (through the setter, so the reset is persisted as well).
if (isElectronSession) {
  electronEnv.avoidMode = "auto";
}
