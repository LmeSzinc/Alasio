/**
 * Main process startup arguments.
 *
 * The desktop client accepts one optional switch, `--window`, selecting
 * how the main window appears on startup:
 *
 *   --window=minimize   start with the window minimized
 *   --window=maximize   start with the window maximized
 *   --window=tray       start into the tray area, the window stays hidden
 *
 * No switch at all — or a value that is none of the three — means the
 * default startup: the window appears in its normal state once the
 * renderer painted its first frame.
 *
 * Name choice (2026-09-11, checked against electron 22.3.27, the version
 * the client ships): electron has no `--window` switch of its own. In the
 * electron binary the token `window` only appears inside chrome's desktop
 * capturer names, NPAPI tags and CSS keyword tables; its `window-*`
 * neighbours are the event / web platform names `window-all-closed`,
 * `window-controls-overlay`, `window-placement` (plus the charset
 * `window-874`), and chrome's `--window-size` / `--window-position` — the
 * two switches a bare `--window` could be confused with — are not part of
 * electron at all (content-shell based, no chrome layer). Measured: a
 * `--window=<value>` reaches the main process untouched, both in
 * `process.argv` and in `app.commandLine`, without any chromium warning.
 * A bare `--window` and `--window-size=…` keep their own switch names, so
 * neither can swallow the other. `--display` was rejected as a name
 * because X11/GTK tools use it for the X server, and electron itself
 * calls monitors "displays" (`screen.getAllDisplays()`); `--show` was
 * rejected because chromium ships a family of `--show-*` debugging
 * switches and "show" is the opposite of minimize/tray.
 *
 * The argv, not `app.commandLine`, is the source of truth: the space
 * separated spelling (`--window tray`) leaves the value in the next argv
 * entry, which `app.commandLine` reports as an empty value.
 */

/** How the main window appears on startup. */
export type StartupWindowMode = "default" | "minimize" | "maximize" | "tray";

/** Switch selecting the startup window mode, spelled `--window=<mode>`. */
export const WINDOW_MODE_SWITCH = "window";

/** Values the switch understands, anything else means the default mode. */
export const WINDOW_MODES = ["minimize", "maximize", "tray"] as const;

/** A requested (non default) startup window mode. */
export type RequestedWindowMode = (typeof WINDOW_MODES)[number];

/**
 * Check a switch value against the supported window modes.
 *
 * @param value Raw switch value
 * @returns True when the value is one of minimize/maximize/tray
 */
export function isStartupWindowMode(value: string): value is RequestedWindowMode {
  return (WINDOW_MODES as readonly string[]).includes(value);
}

/**
 * Parse the startup window mode out of a command line.
 *
 * The last occurrence wins (chromium's own rule for a repeated switch) and
 * an unknown value falls back to the default mode, so a typo never leaves
 * the client without a window.
 *
 * @param argv Command line of the main process (`process.argv`)
 * @returns Requested mode, `"default"` when absent or invalid
 */
export function parseStartupWindowMode(argv: readonly string[]): StartupWindowMode {
  const prefix = `--${WINDOW_MODE_SWITCH}=`;
  const flag = `--${WINDOW_MODE_SWITCH}`;
  let value: string | undefined;
  for (const [index, arg] of argv.entries()) {
    if (arg.startsWith(prefix)) {
      value = arg.slice(prefix.length);
    } else if (arg === flag) {
      value = argv[index + 1];
    }
  }
  return value !== undefined && isStartupWindowMode(value) ? value : "default";
}

/**
 * Startup window mode requested for the current process.
 *
 * @returns Mode requested through the command line
 */
export function getStartupWindowMode(): StartupWindowMode {
  return parseStartupWindowMode(process.argv);
}
