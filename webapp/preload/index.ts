import { contextBridge, ipcRenderer } from "electron";
import {
  IPC_BACKEND_LOG,
  IPC_BACKEND_READY,
  IPC_BACKEND_START,
  IPC_CONFIRM_CLOSE,
  IPC_SHARED_STATE_GET,
  IPC_SHARED_STATE_GET_SYNC,
  IPC_SHARED_STATE_SET_LANGUAGE,
  IPC_SHARED_STATE_SET_THEME,
  IPC_SHARED_STATE_UPDATE,
  IPC_SHUTDOWN_STAGE,
  IPC_WINDOW_CONFIRM_CLOSE,
  IPC_WINDOW_HIDE,
  IPC_WINDOW_MAXIMIZE,
  IPC_WINDOW_MINIMIZE,
} from "../shared/ipc";

const api = {
  // Window controls
  minimizeWindow: () => ipcRenderer.send(IPC_WINDOW_MINIMIZE),
  maximizeWindow: () => ipcRenderer.send(IPC_WINDOW_MAXIMIZE),
  hideWindow: () => ipcRenderer.send(IPC_WINDOW_HIDE),
  confirmClose: () => ipcRenderer.invoke(IPC_WINDOW_CONFIRM_CLOSE),

  // Backend events
  onBackendLog: (callback: (log: string) => void) => {
    const handler = (_: any, log: string) => callback(log);
    ipcRenderer.on(IPC_BACKEND_LOG, handler);
    return () => ipcRenderer.removeListener(IPC_BACKEND_LOG, handler);
  },
  onBackendReady: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on(IPC_BACKEND_READY, handler);
    return () => ipcRenderer.removeListener(IPC_BACKEND_READY, handler);
  },

  // Close flow
  onConfirmClose: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on(IPC_CONFIRM_CLOSE, handler);
    return () => ipcRenderer.removeListener(IPC_CONFIRM_CLOSE, handler);
  },
  onShutdownStage: (callback: (stage: string) => void) => {
    const handler = (_: any, stage: string) => callback(stage);
    ipcRenderer.on(IPC_SHUTDOWN_STAGE, handler);
    return () => ipcRenderer.removeListener(IPC_SHUTDOWN_STAGE, handler);
  },

  // Shared state
  getSharedState: () => ipcRenderer.invoke(IPC_SHARED_STATE_GET),
  // Synchronous variant: read once at renderer startup so the first paint
  // already renders with the host's display theme.
  getSharedStateSync: () => ipcRenderer.sendSync(IPC_SHARED_STATE_GET_SYNC),
  onSharedStateUpdate: (callback: (state: any) => void) => {
    const handler = (_: any, state: any) => callback(state);
    ipcRenderer.on(IPC_SHARED_STATE_UPDATE, handler);
    return () => ipcRenderer.removeListener(IPC_SHARED_STATE_UPDATE, handler);
  },
  setLanguage: (lang: string) => ipcRenderer.send(IPC_SHARED_STATE_SET_LANGUAGE, lang),
  setTheme: (theme: string) => ipcRenderer.send(IPC_SHARED_STATE_SET_THEME, theme),

  // Backend lifecycle
  startBackend: () => ipcRenderer.invoke(IPC_BACKEND_START),
};

// Single source of truth for the API surface exposed to the renderer.
// The renderer imports this type instead of redeclaring the interface.
export type ElectronAPI = typeof api;

contextBridge.exposeInMainWorld("electronAPI", api);
