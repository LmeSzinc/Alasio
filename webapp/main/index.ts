import { app, ipcMain } from 'electron';
import * as path from 'path';
import { loadConfig, saveFirstTimeConfig, AppConfig, ConfigError } from './config';
import { initSharedState, setRoute, setupSharedStateIPC, setMainWindow as setSharedStateWindow } from './shared-state';
import { createWindow, setupWindowIPC } from './window';
import { createTray, setupTrayIPC, setMainWindow as setTrayWindow } from './tray';
import { startBackend, setMainWindow as setBackendWindow } from './backend';

// Disable GPU and configure Electron
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('no-sandbox');
app.commandLine.appendSwitch('disable-http-cache');
app.commandLine.appendSwitch('no-proxy-server');

// Single instance lock
const gotTheLock = app.requestSingleInstanceLock();

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
    // Load configuration
    const configResult = loadConfig();
    
    // Handle config errors
    if ('type' in configResult) {
      const error = configResult as ConfigError;
      initSharedState({
        language: 'en-US',
        webuiPort: 22267,
        route: 'error',
        isFirstTimeSetup: false,
      });
      
      const window = createWindow();
      setSharedStateWindow(window);
      setTrayWindow(window);
      setBackendWindow(window);
      
      setRoute('error', error.message);
      
      setupSharedStateIPC();
      setupWindowIPC();
      setupTrayIPC();
      
      const iconPath = path.join(__dirname, '../resources/icon.png');
      createTray(iconPath, 'en-US');
      return;
    }
    
    const config = configResult as AppConfig;
    
    // Determine initial language
    let initialLang = 'en-US';
    const supportedLangs = ['zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'es-ES'];
    
    if (config.isFirstTimeSetup) {
      // First time: use system language
      const systemLang = app.getLocale();
      initialLang = supportedLangs.includes(systemLang) ? systemLang : 'en-US';
    } else {
      // Second time: use YAML config (or system language as fallback)
      if (config.language && supportedLangs.includes(config.language)) {
        initialLang = config.language;
      } else {
        const systemLang = app.getLocale();
        initialLang = supportedLangs.includes(systemLang) ? systemLang : 'en-US';
      }
    }
    
    // Initialize shared state
    initSharedState({
      language: initialLang,
      webuiPort: config.webuiPort,
      route: config.isFirstTimeSetup ? 'setup' : 'loading',
      isFirstTimeSetup: config.isFirstTimeSetup,
    });
    
    // Create window
    const window = createWindow();
    setSharedStateWindow(window);
    setTrayWindow(window);
    setBackendWindow(window);
    
    // Setup IPC
    setupSharedStateIPC();
    setupWindowIPC();
    setupTrayIPC();
    
    // Handle first-time config save
    ipcMain.handle('config:save-first-time', async (_, language: string) => {
      if (config.templatePath && config.deployPath) {
        await saveFirstTimeConfig(config.templatePath, config.deployPath, language);
        setRoute('loading');
        
        // Start backend after config is saved
        try {
          await startBackend(config.pythonExecutable, config.rootPath);
          setRoute('app');
        } catch (err) {
          console.error('Failed to start backend:', err);
          setRoute('error', 'Failed to start backend');
        }
      }
    });
    
    // Create tray
    const iconPath = path.join(__dirname, '../resources/icon.png');
    createTray(iconPath, initialLang);
    
    // Start backend if not first time setup
    if (!config.isFirstTimeSetup) {
      try {
        await startBackend(config.pythonExecutable, config.rootPath);
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
