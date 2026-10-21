import * as path from "path";
import { BrowserWindow, Menu, Tray, app, nativeImage } from "electron";
import { setLang, t } from "./i18ngen";

let tray: Tray | null = null;
let currentLang = "en-US";
let mainWindow: BrowserWindow | null = null;

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

/**
 * Tray icon file, chosen per platform.
 *
 * Windows takes the multi-size ICO so the shell picks a frame matching
 * the current DPI scale; macOS and Linux cannot read ICO and use the PNG.
 */
function trayIconFile() {
  return process.platform === "win32" ? "icon.ico" : "icon.png";
}

/**
 * Load the tray icon shipped next to the compiled main bundle.
 *
 * vite copies resources/tray/ into the main build output (publicDir in
 * vite.config.mts), so the same path works in development and inside
 * app.asar. An empty image means the asset is missing from the build;
 * the tray would then render as a blank slot, so report it instead of
 * failing silently.
 */
function loadTrayIcon() {
  const file = path.join(__dirname, trayIconFile());
  const image = nativeImage.createFromPath(file);
  if (image.isEmpty()) {
    console.error(`[tray] Failed to load icon: ${file}`);
    return image;
  }
  // macOS menu bar icons are sized in points: the packaging source is
  // 512px and would cover the whole menu bar. Windows keeps the ICO (the
  // shell handles DPI) and Linux tray hosts scale the PNG themselves.
  if (process.platform === "darwin") {
    return image.resize({ width: 16, height: 16 });
  }
  return image;
}

export function createTray(initialLang: string) {
  currentLang = initialLang;
  tray = new Tray(loadTrayIcon());

  tray.setToolTip("Alasio");

  tray.on("click", () => {
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
      },
    },
    {
      label: t.Tray.Hide(),
      click: () => {
        mainWindow?.hide();
      },
    },
    { type: "separator" },
    {
      label: t.Tray.Exit(),
      click: () => {
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
}
