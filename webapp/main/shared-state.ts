import { ipcMain, BrowserWindow } from 'electron';

export type RouteType = 'setup' | 'loading' | 'app' | 'error';

interface SharedState {
  language: string;
  webuiPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
  errorMessage?: string;
}

const state: SharedState = {
  language: 'en-US',
  webuiPort: 22267,
  route: 'loading',
  isFirstTimeSetup: false,
};

let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function initSharedState(config: {
  language: string;
  webuiPort: number;
  route: RouteType;
  isFirstTimeSetup: boolean;
}) {
  state.language = config.language;
  state.webuiPort = config.webuiPort;
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
  notifyRenderer();
}

export function getState(): SharedState {
  return { ...state };
}

function notifyRenderer() {
  if (mainWindow) {
    mainWindow.webContents.send('shared-state:update', state);
  }
}

export function setupSharedStateIPC() {
  ipcMain.handle('shared-state:get', () => state);
  
  ipcMain.on('shared-state:set-language', (_, lang: string) => {
    setLanguage(lang);
  });
}
