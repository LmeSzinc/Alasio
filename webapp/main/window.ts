import * as path from "path";
import { BrowserWindow, app, ipcMain } from "electron";
import {
  IPC_CONFIRM_CLOSE,
  IPC_SHUTDOWN_STAGE,
  IPC_WINDOW_CONFIRM_CLOSE,
  IPC_WINDOW_HIDE,
  IPC_WINDOW_IS_MAXIMIZED,
  IPC_WINDOW_MAXIMIZE,
  IPC_WINDOW_MAXIMIZED_CHANGE,
  IPC_WINDOW_MINIMIZE,
} from "../shared/ipc";
import { appState } from "./app-state";
import { ShutdownStage, setQuittingProbe, shutdownBackend } from "./backend";
import { type StartupWindowMode, getStartupWindowMode } from "./startup-args";

// Minimum size of the main window, in DIP -- the same unit as `width` and
// `height` below and as the renderer's CSS pixels, at any display scaling.
// Both are 480, so the window can be shrunk to a 480x480 square: the smallest
// box the frontend still lays out legibly.
//
// Width: 480 is below the frontend's 640 `sm` breakpoint, so the client
// falls back to the same mobile layout the remote web page serves to a phone
// (nav in a 3/4 width Sheet, header keeping the menu button, title, mod
// selector, language/theme buttons and all 168px of window controls). That
// layout is a first-class one, not a degraded desktop: the drag band still has
// 180px to grab (480 - 56 menu button - 244 for the actions and window
// controls). The compact dev pages keep their own wider min-width and scroll
// sideways, which the layout handles by design.
//
// Height: 480 keeps the 48px title band plus a usable page area, and stays
// below the default 660 so the floor never fills a small screen (a 1366x768
// laptop at 125% scaling has about 1092x574 DIP of work area).
const MIN_WINDOW_WIDTH = 480;
const MIN_WINDOW_HEIGHT = 480;

let mainWindow: BrowserWindow | null = null;
let isQuitting = false;
// In-flight graceful shutdown, shared by every entry point that quits the
// app so a second request (clicking tray Exit twice, or confirming the
// close dialog while the tray is already quitting) joins the first one
// instead of starting a second shutdown of the same backend process.
let quitPromise: Promise<void> | null = null;

// The relaunch path of the backend asks whether the user is already closing
// the app before it takes the exit over (see backend.ts:requestRelaunch)
setQuittingProbe(() => isQuitting);

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
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    frame: false,
    title: "Alasio",
    show: showImmediately,
    // Match the native window background to the display theme (values are
    // the renderer's --background tokens from renderer/app.css: #f3f3f3 for
    // :root and #161618 for .dark) so no white flash appears while the
    // renderer is still loading. The renderer paints its own themed
    // background as soon as it is up.
    backgroundColor: appState.displayTheme === "dark" ? "#161618" : "#f3f3f3",
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

  // Report the maximize state of the window to the renderer. The title bar
  // derives its maximize/restore button from it, and the state can be
  // changed by paths other than those buttons: double-clicking the drag
  // region, window shortcuts, `--window=maximize` at startup. Keeping the
  // renderer in the loop instead of toggling a local flag there means the
  // button always matches the real window.
  mainWindow.on("maximize", notifyMaximizedState);
  mainWindow.on("unmaximize", notifyMaximizedState);

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

/**
 * Push the current maximize state of the main window to the renderer.
 */
function notifyMaximizedState() {
  sendToRenderer(IPC_WINDOW_MAXIMIZED_CHANGE, mainWindow?.isMaximized() ?? false);
}

/**
 * Send a message to the renderer, ignoring windows that are already gone.
 *
 * The shutdown flow keeps talking to the renderer (progress stages) while
 * the window may have been destroyed by another path.
 *
 * Args:
 *     channel (str): IPC channel name
 *     args (list[any]): Payload of the message
 */
function sendToRenderer(channel: string, ...args: unknown[]) {
  const target = mainWindow;
  if (!target || target.isDestroyed()) {
    return;
  }
  target.webContents.send(channel, ...args);
}

/**
 * Quit the app gracefully, without asking the renderer to confirm.
 *
 * The renderer confirmation dialog is for the close button, a single click
 * that is easy to trigger by accident. A tray menu exit is already a
 * deliberate two step action (open the menu, pick Exit), so it quits
 * directly. Both paths share this function: it stops the backend with the
 * same staged shutdown, then destroys the window and quits.
 *
 * Returns:
 *     Promise[None]: Resolves once the backend is down, before the window is
 *     destroyed, so an IPC reply based on it still reaches the renderer
 */
export function quitApp(): Promise<void> {
  if (quitPromise) {
    return quitPromise;
  }
  isQuitting = true;

  quitPromise = new Promise<void>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      // Resolve first so an IPC reply is sent to the renderer before the
      // window is destroyed (otherwise "reply was never sent" error)
      resolve();
      setImmediate(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.destroy();
        }
        app.quit();
      });
    };

    shutdownBackend((stage) => {
      sendToRenderer(IPC_SHUTDOWN_STAGE, stage);
      if (stage === ShutdownStage.Done) {
        finish();
      }
    }).catch((err) => {
      // The user already asked to exit: a backend that cannot be stopped
      // cleanly is reported, not a reason to keep the app open.
      console.error("[window] Failed to shut down the backend:", err);
      finish();
    });
  });

  return quitPromise;
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

  // The renderer reads the state on mount: the window may already be
  // maximized before the title bar subscribes to the push channel.
  ipcMain.handle(IPC_WINDOW_IS_MAXIMIZED, () => mainWindow?.isMaximized() ?? false);

  ipcMain.on(IPC_WINDOW_HIDE, () => {
    mainWindow?.hide();
  });

  ipcMain.handle(IPC_WINDOW_CONFIRM_CLOSE, () => quitApp());
}
