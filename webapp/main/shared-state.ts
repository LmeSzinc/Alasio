import { BrowserWindow, ipcMain } from "electron";
import {
  IPC_SHARED_STATE_GET,
  IPC_SHARED_STATE_GET_SYNC,
  IPC_SHARED_STATE_SET_DPI_SCALING,
  IPC_SHARED_STATE_SET_LANGUAGE,
  IPC_SHARED_STATE_SET_THEME,
  IPC_SHARED_STATE_UPDATE,
} from "../shared/ipc";
import { appState } from "./app-state";
import { updateTrayMenu } from "./tray";

export type RouteType = "setup" | "loading" | "app" | "error";

// Backend startup status (tri-state):
// - "starting": an attempt is in progress, or none happened yet (the
//   setup page before the user starts the first attempt)
// - "success": the current attempt settled without error (hypercorn up)
// - "failed": the last attempt settled with an error
// The loading/setup page shows the running state for "starting", the
// failure hint and a retry action for "failed", and navigates away (via
// setRoute) for "success". Kept in shared state (not a fire-and-forget
// event) because a fast startup failure can happen before the renderer
// mounts; shared state is read synchronously at renderer start, so the
// status is never lost.
export type BackendStatus = "starting" | "success" | "failed";

interface SharedState {
  // Display values (derived, always concrete): what the UI actually shows
  language: string;
  theme: "light" | "dark";
  // Config values (persistent, may be 'system')
  configLang: string;
  configTheme: string;
  // Host-level dpi scaling (single value, no config/display split)
  dpiScaling: boolean;
  backendPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
  // Backend startup status: "starting" while an attempt is in progress
  // (or none happened yet), "success" once the backend is up, "failed"
  // after an attempt settled with an error. See BackendStatus above.
  backendStatus: BackendStatus;
  // Error page payload: errorKey is an i18n key resolved by the renderer
  // (i18n/Error.json), errorPath is an optional filesystem path shown as
  // supplementary detail.
  errorKey?: string;
  errorPath?: string;
}

const state: SharedState = {
  language: "en-US",
  theme: "light",
  configLang: "system",
  configTheme: "system",
  dpiScaling: true,
  backendPort: 22267,
  route: "loading",
  isFirstTimeSetup: false,
  backendStatus: "starting",
};

let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function initSharedState(config: { backendPort: number; route: RouteType; isFirstTimeSetup: boolean }) {
  syncState();
  state.backendPort = config.backendPort;
  state.route = config.route;
  state.isFirstTimeSetup = config.isFirstTimeSetup;
  state.backendStatus = "starting";
}

/**
 * Set the backend startup status. Called by startBackend() on every
 * launch attempt ("starting" at the beginning of an attempt, "failed"
 * when it settles with an error, "success" when it settles without one),
 * so the loading/setup page derives its running/failure states from this
 * flag.
 *
 * Args:
 *     status (BackendStatus): New startup status
 */
export function setBackendStatus(status: BackendStatus) {
  state.backendStatus = status;
  notifyRenderer();
}

export function setRoute(route: RouteType, errorKey?: string, errorPath?: string) {
  state.route = route;
  if (route === "error") {
    if (errorKey) state.errorKey = errorKey;
    if (errorPath) state.errorPath = errorPath;
  } else {
    // Leaving the error route: drop the payload so a later error never
    // shows a stale key/path from a previous failure.
    state.errorKey = undefined;
    state.errorPath = undefined;
  }
  notifyRenderer();
}

/**
 * Set the persistent language through the AppState singleton, which is
 * the single source of truth (derives display, broadcasts to the backend
 * through stdin, notifies listeners).
 */
export function setLanguage(lang: string) {
  appState.setLang(lang);
}

/**
 * Set the persistent theme through the AppState singleton. Same flow as
 * setLanguage.
 */
export function setTheme(theme: string) {
  appState.setTheme(theme);
}

/**
 * Set the persistent dpi scaling through the AppState singleton, which
 * is the single source of truth. Same flow as setLanguage/setTheme.
 */
export function setDpiScaling(dpiScaling: boolean) {
  appState.setDpiScaling(dpiScaling);
}

export function getState(): SharedState {
  return { ...state };
}

function syncState() {
  state.language = appState.displayLang;
  state.theme = appState.displayTheme;
  state.configLang = appState.configLang;
  state.configTheme = appState.configTheme;
  state.dpiScaling = appState.dpiScaling;
}

function notifyRenderer() {
  if (mainWindow) {
    mainWindow.webContents.send(IPC_SHARED_STATE_UPDATE, state);
  }
}

export function setupSharedStateIPC() {
  ipcMain.handle(IPC_SHARED_STATE_GET, () => state);

  // Synchronous variant: used once by the renderer at startup so the first
  // paint already shows the host's display theme before the async invoke
  // round trip resolves.
  ipcMain.on(IPC_SHARED_STATE_GET_SYNC, (event) => {
    event.returnValue = state;
  });

  ipcMain.on(IPC_SHARED_STATE_SET_LANGUAGE, (_, lang: string) => {
    setLanguage(lang);
  });

  ipcMain.on(IPC_SHARED_STATE_SET_THEME, (_, theme: string) => {
    setTheme(theme);
  });

  ipcMain.on(IPC_SHARED_STATE_SET_DPI_SCALING, (_, dpiScaling: boolean) => {
    setDpiScaling(dpiScaling);
  });
}

// Keep the tray menu and the renderer state in sync with the AppState
// singleton. Registered at module load: AppState is created before this
// module is imported (main/index.ts imports app-state first), and the
// callbacks guard against not-yet-created resources.
appState.onChange(() => {
  updateTrayMenu(appState.displayLang);
  syncState();
  notifyRenderer();
});
