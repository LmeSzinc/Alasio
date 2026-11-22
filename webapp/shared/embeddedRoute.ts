// The route of the embedded web app, remembered across a window reload.
//
// The window draws only the shell; the web app of the backend lives in an
// <iframe> that drives its own client-side routes (/config/<name>/overview,
// ...). A reload of the window (Ctrl+R, Shift+Ctrl+R) remounts that iframe at
// the URL in its `src` attribute, and the frontend root opens the first
// configuration: the configuration and the page the user was on would be lost.
//
// So the frontend reports the route it is on (the `alasio:route` uplink), the
// main process keeps the last one in memory, and the iframe is mounted at it
// again. The host parameters of the mount URL (?embedded=electron, theme,
// language, controls width) are rebuilt from the current display values
// instead of being replayed, so a reload never restores a stale theme or
// language -- and the restored URL still tells the frontend it runs inside the
// client.
//
// The state lives in the main process on purpose: it has to outlive the
// renderer (a reload restarts it) but not the app (a new launch starts at the
// app root again), which is exactly the lifetime of a main process variable.
// Page storage would be the wrong tool: Chromium writes sessionStorage to disk
// (a LevelDB in the profile) even though Electron never reads it back, so it
// would leave the route behind as garbage without ever restoring it.
//
// This module is shared by all three processes: the main process validates the
// uplink, the renderer validates before it sends and before it mounts.

/** Longest route kept; a longer report is not a page of this app */
const MAX_ROUTE_LENGTH = 2048;

/**
 * Whether a reported route can be mounted as a page of the embedded frontend.
 *
 * Only a path of this frontend is accepted: a string starting with a single
 * `/`. `//host` and `/\host` are other origins rather than paths, an empty
 * string is no page, and a control character means the report is not a route
 * at all.
 *
 * Args:
 *   route: Reported route
 *
 * Returns:
 *   bool: True when the route is a path of the embedded frontend
 */
export function isEmbeddedRoute(route: unknown): route is string {
  if (typeof route !== "string") return false;
  if (route.length === 0 || route.length > MAX_ROUTE_LENGTH) return false;
  if (!route.startsWith("/")) return false;
  if (route.startsWith("//") || route.startsWith("/\\")) return false;
  return !/[\u0000-\u001f\u007f]/.test(route);
}

/**
 * URL the iframe is mounted at.
 *
 * The route keeps its own query and hash; the host parameters are set on top,
 * so a remembered `?dev=1` does not swallow `?embedded=electron`.
 *
 * Args:
 *   port: Port of the local backend
 *   route: Route to open, `/` for the app root
 *   params: Host parameters of the mount URL (?embedded=electron, theme, ...)
 *
 * Returns:
 *   string: Absolute URL of the embedded frontend page
 */
export function embeddedUrl(port: number, route: string, params: Record<string, string | number>): string {
  const url = new URL(isEmbeddedRoute(route) ? route : "/", `http://127.0.0.1:${port}`);
  for (const [name, value] of Object.entries(params)) {
    url.searchParams.set(name, String(value));
  }
  return url.href;
}
