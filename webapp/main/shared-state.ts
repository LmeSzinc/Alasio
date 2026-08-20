import { ipcMain, BrowserWindow } from 'electron';
import {
  IPC_SHARED_STATE_GET,
  IPC_SHARED_STATE_SET_LANGUAGE,
  IPC_SHARED_STATE_SET_THEME,
  IPC_SHARED_STATE_UPDATE,
} from '../shared/ipc';
import { appState } from './app-state';
import { updateTrayMenu } from './tray';

export type RouteType = 'setup' | 'loading' | 'app' | 'error';

interface SharedState {
  // Display values (derived, always concrete): what the UI actually shows
  language: string;
  theme: 'light' | 'dark';
  // Config values (persistent, may be 'system')
  configLang: string;
  configTheme: string;
  backendPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
  errorMessage?: string;
}

const state: SharedState = {
  language: 'en-US',
  theme: 'light',
  configLang: 'system',
  configTheme: 'system',
  backendPort: 22267,
  route: 'loading',
  isFirstTimeSetup: false,
};

let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function initSharedState(config: {
  backendPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
}) {
  syncState();
  state.backendPort = config.backendPort;
  state.route = config.route;
  state.isFirstTimeSetup = config.isFirstTimeSetup;
}

export function setRoute(route: RouteType, errorMessage?: string) {
  state.route = route;
  if (errorMessage) state.errorMessage = errorMessage;
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

export function getState(): SharedState {
  return { ...state };
}

function syncState() {
  state.language = appState.displayLang;
  state.theme = appState.displayTheme;
  state.configLang = appState.configLang;
  state.configTheme = appState.configTheme;
}

function notifyRenderer() {
  if (mainWindow) {
    mainWindow.webContents.send(IPC_SHARED_STATE_UPDATE, state);
  }
}

export function setupSharedStateIPC() {
  ipcMain.handle(IPC_SHARED_STATE_GET, () => state);

  ipcMain.on(IPC_SHARED_STATE_SET_LANGUAGE, (_, lang: string) => {
    setLanguage(lang);
  });

  ipcMain.on(IPC_SHARED_STATE_SET_THEME, (_, theme: string) => {
    setTheme(theme);
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
