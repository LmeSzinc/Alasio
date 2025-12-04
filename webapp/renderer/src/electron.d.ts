interface ElectronAPI {
  minimizeWindow: () => void;
  maximizeWindow: () => void;
  hideWindow: () => void;
  confirmClose: () => Promise<void>;
  onBackendLog: (callback: (log: string) => void) => () => void;
  onBackendReady: (callback: () => void) => () => void;
  onConfirmClose: (callback: () => void) => () => void;
  onShutdownStage: (callback: (stage: string) => void) => () => void;
  getSharedState: () => Promise<any>;
  onSharedStateUpdate: (callback: (state: any) => void) => () => void;
  setLanguage: (lang: string) => void;
  updateTrayLanguage: (lang: string) => void;
  saveFirstTimeConfig: (language: string) => Promise<void>;
}

declare global {
  interface Window {
    electronAPI: ElectronAPI;
  }
}

export {};
