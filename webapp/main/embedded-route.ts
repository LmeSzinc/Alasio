/**
 * The route of the embedded web app, held in the main process.
 *
 * A window reload restarts the renderer but not the main process, so a value
 * kept here survives exactly the reload it has to survive -- and dies with the
 * app, so a new launch still opens the app root. The validation and the mount
 * URL live in `../shared/embeddedRoute`, which the renderer uses as well.
 */
import { ipcMain } from "electron";
import { isEmbeddedRoute } from "../shared/embeddedRoute";
import { IPC_EMBEDDED_ROUTE_GET_SYNC, IPC_EMBEDDED_ROUTE_SET } from "../shared/ipc";

/**
 * Store of the route the embedded page is on.
 *
 * A report that is not a path of the frontend is dropped and the previous
 * route stays, so a malformed message can never point the next mount at
 * another origin.
 */
export function createEmbeddedRouteStore() {
  let route: string | null = null;

  return {
    /**
     * Remember the route of the embedded page.
     *
     * Args:
     *   value: Reported route
     */
    remember(value: unknown) {
      if (!isEmbeddedRoute(value)) return;
      route = value;
    },

    /**
     * The route the embedded page was on, null when it never reported one.
     *
     * Returns:
     *   str | None: Remembered route
     */
    get(): string | null {
      return route;
    },
  };
}

/** The store of this app run */
export const embeddedRouteStore = createEmbeddedRouteStore();

/**
 * Register the IPC channels of the remembered route.
 *
 * Must run before the window is created: the renderer reads the route
 * synchronously while its first page renders, and a missing handler answers
 * `undefined` instead of the remembered value.
 */
export function setupEmbeddedRouteIPC() {
  ipcMain.on(IPC_EMBEDDED_ROUTE_SET, (_, value: unknown) => {
    embeddedRouteStore.remember(value);
  });

  ipcMain.on(IPC_EMBEDDED_ROUTE_GET_SYNC, (event) => {
    event.returnValue = embeddedRouteStore.get();
  });
}
