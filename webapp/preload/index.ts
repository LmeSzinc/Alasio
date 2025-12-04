import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  // Window controls
  minimizeWindow: () => ipcRenderer.send('window:minimize'),
  maximizeWindow: () => ipcRenderer.send('window:maximize'),
  hideWindow: () => ipcRenderer.send('window:hide'),
  confirmClose: () => ipcRenderer.invoke('window:confirm-close'),
  
  // Backend events
  onBackendLog: (callback: (log: string) => void) => {
    const handler = (_: any, log: string) => callback(log);
    ipcRenderer.on('backend:log', handler);
    return () => ipcRenderer.removeListener('backend:log', handler);
  },
  onBackendReady: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on('backend:ready', handler);
    return () => ipcRenderer.removeListener('backend:ready', handler);
  },
  
  // Close flow
  onConfirmClose: (callback: () => void) => {
    const handler = () => callback();
    ipcRenderer.on('confirm-close', handler);
    return () => ipcRenderer.removeListener('confirm-close', handler);
  },
  onShutdownStage: (callback: (stage: string) => void) => {
    const handler = (_: any, stage: string) => callback(stage);
    ipcRenderer.on('shutdown:stage', handler);
    return () => ipcRenderer.removeListener('shutdown:stage', handler);
  },
  
  // Shared state
  getSharedState: () => ipcRenderer.invoke('shared-state:get'),
  onSharedStateUpdate: (callback: (state: any) => void) => {
    const handler = (_: any, state: any) => callback(state);
    ipcRenderer.on('shared-state:update', handler);
    return () => ipcRenderer.removeListener('shared-state:update', handler);
  },
  setLanguage: (lang: string) => ipcRenderer.send('shared-state:set-language', lang),
  
  // Tray
  updateTrayLanguage: (lang: string) => ipcRenderer.send('tray:update-language', lang),
  
  // First-time config
  saveFirstTimeConfig: (language: string) => 
    ipcRenderer.invoke('config:save-first-time', language),
});
