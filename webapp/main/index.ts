import { app, ipcMain } from 'electron';
import * as path from 'path';
import { loadConfig } from './config';
import { appState } from './app-state';
import { initSharedState, setRoute, setupSharedStateIPC, setMainWindow as setSharedStateWindow } from './shared-state';
import { createWindow, setupWindowIPC } from './window';
import { createTray, setMainWindow as setTrayWindow } from './tray';
import { startBackend, setMainWindow as setBackendWindow } from './backend';
import { IPC_BACKEND_START } from '../shared/ipc';
import { registerAppProtocol } from './protocol';

// Disable GPU and configure Electron
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-http-cache');
app.commandLine.appendSwitch('no-proxy-server');

// Single instance lock
const gotTheLock = app.requestSingleInstanceLock();

// Register the app:// protocol serving the built renderer (must be before app ready)
registerAppProtocol(path.join(__dirname, '../renderer'));

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const win = require('./window').getMainWindow();
    if (win) {
      if (win.isMinimized()) win.restore();
      if (!win.isVisible()) win.show();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    // Load configuration into the AppState singleton. On failure only
    // appState.configError is set and the error page is shown below.
    loadConfig();
    // Register the nativeTheme listener and derive the initial display
    // values (must happen after app ready).
    appState.init();

    // Handle config errors
    if (appState.configError) {
      initSharedState({
        backendPort: 22267,
        route: 'error',
        isFirstTimeSetup: false,
      });

      const window = createWindow();
      setSharedStateWindow(window);
      setTrayWindow(window);
      setBackendWindow(window);

      setRoute('error', appState.configError.message);

      setupSharedStateIPC();
      setupWindowIPC();

      const iconPath = path.join(__dirname, '../resources/icon.png');
      createTray(iconPath, appState.displayLang);
      return;
    }

    // Initialize shared state
    initSharedState({
      backendPort: appState.backendPort,
      route: appState.isFirstTimeSetup ? 'setup' : 'loading',
      isFirstTimeSetup: appState.isFirstTimeSetup,
    });

    // Create window
    const window = createWindow();
    setSharedStateWindow(window);
    setTrayWindow(window);
    setBackendWindow(window);

    // Setup IPC
    setupSharedStateIPC();
    setupWindowIPC();

    // Start the backend on first-time setup: the setup page has already
    // saved the language/theme into the AppState (via setLanguage/
    // setTheme IPC), and the backend persists them into deploy.yaml once
    // it is ready (broadcastPrefs).
    ipcMain.handle(IPC_BACKEND_START, async () => {
      try {
        await startBackend(appState.pythonExecutable, appState.rootPath, appState.backendHost, appState.backendPort);
        setRoute('app');
      } catch (err) {
        console.error('Failed to start backend:', err);
        setRoute('error', 'Failed to start backend');
      }
    });

    // Create tray
    const iconPath = path.join(__dirname, '../resources/icon.png');
    createTray(iconPath, appState.displayLang);

    // Start backend if not first time setup
    if (!appState.isFirstTimeSetup) {
      try {
        await startBackend(appState.pythonExecutable, appState.rootPath, appState.backendHost, appState.backendPort);
        setRoute('app');
      } catch (err) {
        console.error('Failed to start backend:', err);
        setRoute('error', 'Failed to start backend');
      }
    }
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });
}
