import * as path from "path";
import { BrowserWindow, app, ipcMain } from "electron";
import {
  IPC_CONFIRM_CLOSE,
  IPC_SHUTDOWN_STAGE,
  IPC_WINDOW_CONFIRM_CLOSE,
  IPC_WINDOW_HIDE,
  IPC_WINDOW_MAXIMIZE,
  IPC_WINDOW_MINIMIZE,
} from "../shared/ipc";
import { appState } from "./app-state";
import { ShutdownStage, shutdownBackend } from "./backend";
import { type StartupWindowMode, getStartupWindowMode } from "./startup-args";

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;

export function createWindow(): BrowserWindow {
  // The window is created hidden in production and revealed once the
  // renderer painted its first frame (ready-to-show): the window never
  // appears with an empty/loading frame — whatever route (loading/setup/
  // app/error) the first paint lands on is already complete. In dev mode
  // the window shows immediately — the developer drives navigation
  // manually through the dev route switcher and expects the window right
  // away. An explicit --window mode overrides the dev shortcut as well: it
  // is a deliberate choice, and the only way to exercise the startup modes
  // against the dev server.
  const isDev = !!process.env.VITE_DEV_SERVER_URL;
  const windowMode = getStartupWindowMode();
  const showImmediately = isDev && windowMode === "default";
  mainWindow = new BrowserWindow({
    width: 960,
    height: 660,
    frame: false,
    title: "Alasio",
    show: showImmediately,
    // Match the native window background to the display theme (values are
    // the renderer's --background tokens) so no white flash appears while
    // the renderer is still loading. The renderer paints its own themed
    // background as soon as it is up.
    backgroundColor: appState.displayTheme === "dark" ? "#18181b" : "#f3f3f3",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Reveal the window once the renderer painted its first frame (see the
  // comment above). Route switches themselves are emitted by the main
  // process (setRoute -> shared-state:update), so no renderer round-trip
  // is needed to time the reveal.
  //
  // DO NOT remove this gate as an "optimization" (eager show). Root cause
  // of the first-launch flicker it prevents (2026-09-03): on Windows, a
  // window that never composited a frame has no surface content, so DWM
  // shows whatever is behind it (the desktop) inside the window area.
  // This app disables hardware acceleration
  // (app.disableHardwareAcceleration in main/index.ts), so composition
  // runs on the software path whose cold start is slow (no shader/disk
  // cache), and the first frame arrives late. Showing the window during
  // that window toggles between "no frame (desktop shows through)" and
  // "first frame committed (themed background)" at vsync rate, which
  // reads as high-frequency flicker on the very first launch. Later
  // launches are warm and deliver the first frame within tens of
  // milliseconds, hiding the race and making the flicker look like an
  // occasional glitch that is safe to "fix" away.
  //
  // Note on the Electron 22 semantics: ready-to-show fires on the FIRST
  // NON-EMPTY LAYOUT of the main frame (WebContents::OnFirstNonEmptyLayout
  // in shell/browser/api/electron_api_web_contents.cc), NOT on the first
  // OS-composited frame. It is therefore the earliest safe moment to
  // show: whatever route (loading/setup/app/error) the first layout lands
  // on is already complete, and showing any earlier would re-open the
  // desktop-through race above.
  if (!showImmediately) {
    mainWindow.once("ready-to-show", () => {
      showWindow(windowMode);
    });
  }

  // Load renderer
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
  } else {
    // app:// is registered by main/index.ts; the custom scheme is required
    // because SvelteKit's client-side router uses history.pushState, which
    // fails on the file:// protocol. The root path maps to index.html.
    mainWindow.loadURL("app://bundle/");
  }

  // Prevent default close behavior
  mainWindow.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow?.webContents.send(IPC_CONFIRM_CLOSE);
    }
  });

  // Navigation interception: the window only
  // ever loads the app://bundle (or the dev server) URL. Any other
  // navigation — a compromised renderer steering the window to an
  // external page — is blocked. In-app navigation happens inside the
  // iframe (client-side routing), so the top frame never needs to move.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    const current = mainWindow?.webContents.getURL() ?? "";
    if (url !== current) {
      event.preventDefault();
    }
  });

  // window.open / target=_blank from the renderer: deny everything. The
  // embedded app never needs popups; external links are handled by the
  // frontend itself (if any) or simply do not work.
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  return mainWindow;
}

/**
 * Reveal the created window in the requested startup mode.
 *
 * The window is brought into its startup state *before* it can be seen,
 * never after: `--window=minimize` must not show the window first and
 * minimize it afterwards, and the same holds for `--window=maximize`.
 * Measured on windows with electron 22.3.27, sampling a hidden window
 * every 5ms:
 *
 * - `minimize()` shows the window already minimized (the very first
 *   sample reads visible + minimized, no intermediate normal state). A
 *   `show()` afterwards would undo it — it emits `restore` and leaves the
 *   window un-minimized — so it is not called here.
 * - `maximize()` shows the window already maximized (same argument). The
 *   `show()` afterwards keeps the maximized state and adds the focus,
 *   matching what the default mode does.
 * - `tray` never shows the window at all: it stays hidden until the user
 *   picks "Show" in the tray menu, or launches the client a second time
 *   (the second-instance handler in main/index.ts reveals the window).
 *
 * @param mode Startup mode requested through the command line
 */
function showWindow(mode: StartupWindowMode) {
  const target = mainWindow;
  if (!target || mode === "tray") {
    return;
  }
  if (mode === "minimize") {
    target.minimize();
    return;
  }
  if (mode === "maximize") {
    target.maximize();
  }
  target.show();
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
