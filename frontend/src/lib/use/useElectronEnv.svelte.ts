// Electron environment detection for the embedded web app.
//
// The webapp loads the frontend in an iframe with `?embedded=electron` and
// overlays its window controls (hide/minimize/maximize/close) at the top
// right of the page. The frontend can reserve space for them in its own
// header (see `electronEnv.shouldAvoid`).
//
// UI-only flags: everything here can be tampered with by the user and it
// only affects layout, which is exactly what we want for debugging.
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
export const isElectronSession = new URLSearchParams(location.search).get("embedded") === "electron";

// embedded: always mirrors the persisted value; an electron session forces
// true on every load (overriding whatever was stored).
const embedded = $state(isElectronSession || readStored(EMBEDDED_KEY, false));
if (isElectronSession) {
  writeStored(EMBEDDED_KEY, true);
}

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
    return avoidMode === "always" || (avoidMode === "auto" && embedded);
  },
};

// Electron session: reset the avoidance mode to the default on every load
// (through the setter, so the reset is persisted as well).
if (isElectronSession) {
  electronEnv.avoidMode = "auto";
}
