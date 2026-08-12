// IPC channel names, shared by the main process, preload and renderer types.
// Channels are declared once here so that a typo can never silently break a
// channel across the process boundary.

// Renderer -> Main, fire-and-forget (ipcRenderer.send / ipcMain.on)
export const IPC_WINDOW_MINIMIZE = 'window:minimize';
export const IPC_WINDOW_MAXIMIZE = 'window:maximize';
export const IPC_WINDOW_HIDE = 'window:hide';
export const IPC_SHARED_STATE_SET_LANGUAGE = 'shared-state:set-language';

// Renderer -> Main, request/response (ipcRenderer.invoke / ipcMain.handle)
export const IPC_WINDOW_CONFIRM_CLOSE = 'window:confirm-close';
export const IPC_SHARED_STATE_GET = 'shared-state:get';
export const IPC_CONFIG_SAVE_FIRST_TIME = 'config:save-first-time';

// Main -> Renderer, events (webContents.send / ipcRenderer.on)
export const IPC_BACKEND_LOG = 'backend:log';
export const IPC_BACKEND_READY = 'backend:ready';
export const IPC_CONFIRM_CLOSE = 'confirm-close';
export const IPC_SHUTDOWN_STAGE = 'shutdown:stage';
export const IPC_SHARED_STATE_UPDATE = 'shared-state:update';
