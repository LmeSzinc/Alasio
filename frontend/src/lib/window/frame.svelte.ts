// Window controls geometry of the electron host, as seen by the embedded
// frontend.
//
// The webapp is frameless and draws the window controls (hide / minimize /
// maximize / close) over the frontend header, on top of the iframe. The
// frontend has to keep them clear: `AppHeader` reserves `frame.controls.width`
// on the right, and the drag region reporter adds the same width to the right
// inset it sends back.
//
// In a plain browser (`vite dev`, or a remote visitor) there are no window
// controls. `simulateMode` exists so a `vite dev` page can be laid out like
// the webapp anyway: it decides what the width falls back to when no host
// value arrived (see resolveControlsWidth()).
import { browser } from "$app/environment";
import { isElectronSession } from "$lib/use/useElectronEnv.svelte";
import { readStored, writeStored } from "./storage";

/**
 * Reserve width used when the host never reported one: hide + minimize +
 * maximize + close (4 * 36) + their gaps (3 * 4) + the right margin (6) + the
 * matching 6px gap on the left of the group.
 */
export const FALLBACK_CONTROLS_WIDTH = 168;

/**
 * - `auto`: follow the host. A browser (vite dev, a remote visitor) has no host,
 *   so nothing is reserved; a page opened with `?embedded=electron` still gets
 *   the value the host puts in the iframe URL.
 * - `always`: simulate the fallback width, so a plain `vite dev` page can be laid
 *   out like the webapp without a host
 * - `never`: reserve nothing, the remote-browser layout
 */
export type WindowControlsSimulateMode = "auto" | "always" | "never";

/** Where the effective width came from, shown by the dev tools */
export type ControlsSource = "host" | "url" | "simulated" | "none";

export interface ControlsResolution {
  width: number;
  source: ControlsSource;
}

const SIMULATE_KEY = "alasio-window-controls-simulate";

/**
 * Effective reserve width of the window controls.
 *
 * The chain is host measurement, then the iframe URL value (first paint, before
 * the downlink arrives), then a fallback decided by the mode. Only `always`
 * falls back: in a browser there is no host to follow, so `auto` reserves
 * nothing. A browser that merely entered the webapp once before must not keep
 * the padding -- otherwise `auto` stops meaning "follow the host".
 *
 * Args:
 *   input: Current values of the chain, see ControlsResolution for the output
 */
export function resolveControlsWidth(input: {
  mode: WindowControlsSimulateMode;
  host: number | null;
  url: number | null;
}): ControlsResolution {
  if (input.mode === "never") return { width: 0, source: "none" };
  if (input.host !== null) return { width: input.host, source: "host" };
  if (input.url !== null) return { width: input.url, source: "url" };
  return input.mode === "always"
    ? { width: FALLBACK_CONTROLS_WIDTH, source: "simulated" }
    : { width: 0, source: "none" };
}

/** `?controlsWidth=` of the iframe URL, `null` when absent or invalid */
function readUrlControlsWidth(): number | null {
  if (!browser) return null;
  const raw = new URLSearchParams(location.search).get("controlsWidth");
  if (raw === null) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

const urlControlsWidth = readUrlControlsWidth();

// Measured (or reported) width of the host; `null` until the first downlink.
let hostControlsWidth = $state<number | null>(null);

let simulateMode = $state<WindowControlsSimulateMode>(browser ? readStored(SIMULATE_KEY, "auto") : "auto");

export const frame = {
  /** Reserve width of the host window controls */
  get controls(): ControlsResolution {
    return resolveControlsWidth({
      mode: simulateMode,
      host: hostControlsWidth,
      url: urlControlsWidth,
    });
  },

  get simulateMode() {
    return simulateMode;
  },

  set simulateMode(mode: WindowControlsSimulateMode) {
    simulateMode = mode;
    writeStored(SIMULATE_KEY, mode);
  },
};

if (browser) {
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window.parent) return;
    const data = event.data;
    if (!data || typeof data !== "object" || data.type !== "alasio:frame") return;
    const width = (data as { controls?: { width?: unknown } }).controls?.width;
    if (typeof width !== "number" || !Number.isFinite(width) || width < 0) return;
    hostControlsWidth = width;
  });
}

// An electron session always uses the host value: a `never` left over from a
// dev session must not strip the padding inside the real client.
if (isElectronSession) {
  frame.simulateMode = "auto";
}
