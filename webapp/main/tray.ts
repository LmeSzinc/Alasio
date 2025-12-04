import { Tray, Menu, nativeImage, app, ipcMain, BrowserWindow } from 'electron';
import * as path from 'path';
import { getTrayTranslations } from './i18n';

let tray: Tray | null = null;
let currentLang = 'en-US';
let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function createTray(iconPath: string, initialLang: string) {
  currentLang = initialLang;
  const icon = nativeImage.createFromPath(iconPath);
  tray = new Tray(icon);
  
  tray.setToolTip('Alasio');
  
  tray.on('click', () => {
    if (mainWindow?.isVisible()) {
      mainWindow.hide();
    } else {
      mainWindow?.show();
      mainWindow?.focus();
    }
  });
  
  updateTrayMenu(currentLang);
  return tray;
}

export function updateTrayMenu(lang: string) {
  if (!tray) return;
  
  currentLang = lang;
  const t = getTrayTranslations(lang);
  
  const contextMenu = Menu.buildFromTemplate([
    {
      label: t.show,
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      }
    },
    {
      label: t.hide,
      click: () => {
        mainWindow?.hide();
      }
    },
    { type: 'separator' },
    {
      label: t.exit,
      click: () => {
        app.quit();
      }
    }
  ]);
  
  tray.setContextMenu(contextMenu);
}

export function setupTrayIPC() {
  ipcMain.on('tray:update-language', (_, lang: string) => {
    updateTrayMenu(lang);
  });
}
