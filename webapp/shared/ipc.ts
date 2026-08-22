// IPC channel names, shared by the main process, preload and renderer types.
// Channels are declared once here so that a typo can never silently break a
// channel across the process boundary.

// Renderer -> Main, fire-and-forget (ipcRenderer.send / ipcMain.on)
export const IPC_WINDOW_MINIMIZE = "window:minimize";
export const IPC_WINDOW_MAXIMIZE = "window:maximize";
export const IPC_WINDOW_HIDE = "window:hide";
export const IPC_SHARED_STATE_SET_LANGUAGE = "shared-state:set-language";
export const IPC_SHARED_STATE_SET_THEME = "shared-state:set-theme";

// Renderer -> Main, request/response (ipcRenderer.invoke / ipcMain.handle)
export const IPC_WINDOW_CONFIRM_CLOSE = "window:confirm-close";
export const IPC_SHARED_STATE_GET = "shared-state:get";
export const IPC_BACKEND_START = "backend:start";

// Renderer -> Main, synchronous request/response (ipcRenderer.sendSync /
// ipcMain.on + event.returnValue). Read once at renderer startup so the
// first paint already renders with the host's display theme (no light
// flash before the async IPC round trip resolves).
export const IPC_SHARED_STATE_GET_SYNC = "shared-state:get-sync";

// Main -> Renderer, events (webContents.send / ipcRenderer.on)
export const IPC_BACKEND_LOG = "backend:log";
export const IPC_BACKEND_READY = "backend:ready";
export const IPC_CONFIRM_CLOSE = "confirm-close";
export const IPC_SHUTDOWN_STAGE = "shutdown:stage";
export const IPC_SHARED_STATE_UPDATE = "shared-state:update";
