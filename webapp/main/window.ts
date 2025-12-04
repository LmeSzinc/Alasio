import { BrowserWindow, app, ipcMain } from 'electron';
import * as path from 'path';
import { shutdownBackend, ShutdownStage } from './backend';

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;

export function createWindow(): BrowserWindow {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
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
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  // Prevent default close behavior
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.webContents.send('confirm-close');
    }
  });

  return mainWindow;
}

export function getMainWindow(): BrowserWindow | null {
  return mainWindow;
}

export function setupWindowIPC() {
  ipcMain.on('window:minimize', () => {
    mainWindow?.minimize();
  });

  ipcMain.on('window:maximize', () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });

  ipcMain.on('window:hide', () => {
    mainWindow?.hide();
  });

  ipcMain.handle('window:confirm-close', async () => {
    isQuitting = true;
    
    return new Promise<void>((resolve) => {
      shutdownBackend((stage) => {
        mainWindow?.webContents.send('shutdown:stage', stage);
        
        if (stage === ShutdownStage.Done) {
          mainWindow?.destroy();
          app.quit();
          resolve();
        }
      });
    });
  });
}
