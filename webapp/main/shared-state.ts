import { ipcMain, BrowserWindow } from 'electron';
import {
  IPC_SHARED_STATE_GET,
  IPC_SHARED_STATE_SET_LANGUAGE,
  IPC_SHARED_STATE_UPDATE,
} from '../shared/ipc';
import { updateTrayMenu } from './tray';

export type RouteType = 'setup' | 'loading' | 'app' | 'error';

interface SharedState {
  language: string;
  backendPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
  errorMessage?: string;
}

const state: SharedState = {
  language: 'en-US',
  backendPort: 22267,
  route: 'loading',
  isFirstTimeSetup: false,
};

let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function initSharedState(config: {
  language: string;
  backendPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
}) {
  state.language = config.language;
  state.backendPort = config.backendPort;
  state.route = config.route;
  state.isFirstTimeSetup = config.isFirstTimeSetup;
}

export function setRoute(route: RouteType, errorMessage?: string) {
  state.route = route;
  if (errorMessage) state.errorMessage = errorMessage;
  notifyRenderer();
}

export function setLanguage(lang: string) {
  state.language = lang;
  updateTrayMenu(lang);
  notifyRenderer();
}

export function getState(): SharedState {
  return { ...state };
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
}
