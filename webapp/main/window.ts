import { BrowserWindow, app, ipcMain } from 'electron';
import * as path from 'path';
import {
  IPC_CONFIRM_CLOSE,
  IPC_SHUTDOWN_STAGE,
  IPC_WINDOW_CONFIRM_CLOSE,
  IPC_WINDOW_HIDE,
  IPC_WINDOW_MAXIMIZE,
  IPC_WINDOW_MINIMIZE,
} from '../shared/ipc';
import { shutdownBackend, ShutdownStage } from './backend';

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;

export function createWindow(): BrowserWindow {
  mainWindow = new BrowserWindow({
    width: 960,
    height: 660,
    frame: false,
    title: 'Alasio',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Load renderer
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
  } else {
    // app:// is registered by main/index.ts; the custom scheme is required
    // because SvelteKit's client-side router uses history.pushState, which
    // fails on the file:// protocol. The root path maps to index.html.
    mainWindow.loadURL('app://bundle/');
  }

  // Prevent default close behavior
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.webContents.send(IPC_CONFIRM_CLOSE);
    }
  });

  return mainWindow;
}

export function getMainWindow(): BrowserWindow | null {
  return mainWindow;
}

export function setupWindowIPC() {
  ipcMain.on(IPC_WINDOW_MINIMIZE, () => {
    mainWindow?.minimize();
  });

  ipcMain.on(IPC_WINDOW_MAXIMIZE, () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });

  ipcMain.on(IPC_WINDOW_HIDE, () => {
    mainWindow?.hide();
  });

  ipcMain.handle(IPC_WINDOW_CONFIRM_CLOSE, async () => {
    isQuitting = true;
    
    return new Promise<void>((resolve) => {
      shutdownBackend((stage) => {
        mainWindow?.webContents.send(IPC_SHUTDOWN_STAGE, stage);
        
        if (stage === ShutdownStage.Done) {
          // Resolve first so the IPC reply is sent to the renderer
          // before destroying the window (otherwise "reply was never sent" error)
          resolve();
          setImmediate(() => {
            mainWindow?.destroy();
            app.quit();
          });
        }
      });
    });
  });
}