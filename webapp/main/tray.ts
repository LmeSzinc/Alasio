import { Tray, Menu, nativeImage, app, BrowserWindow } from 'electron';
import * as path from 'path';
import { setLang, t } from './i18ngen';

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
  // Node-mode i18n: the generated translation functions read the current
  // language through getLang(), so set it before reading t.Tray.*
  setLang(lang);
  
  const contextMenu = Menu.buildFromTemplate([
    {
      label: t.Tray.Show(),
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      }
    },
    {
      label: t.Tray.Hide(),
      click: () => {
        mainWindow?.hide();
      }
    },
    { type: 'separator' },
    {
      label: t.Tray.Exit(),
      click: () => {
        app.quit();
      }
    }
  ]);
  
  tray.setContextMenu(contextMenu);
}
