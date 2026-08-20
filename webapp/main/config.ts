import * as path from 'path';
import * as fs from 'fs';
import * as yaml from 'js-yaml';
import { appState, isConfigLang, isConfigTheme } from './app-state';

interface DeployConfig {
  Python?: {
    PythonExecutable?: string;
  };
  Backend?: {
    Host?: string;
    Port?: number;
  };
  Webapp?: {
    Lang?: string;
    Theme?: string;
  };
}

export interface ConfigError {
  type: 'config_not_found' | 'python_not_found' | 'guipy_not_found';
  message: string;
  currentPath: string;
}

// Search for deploy.yaml or deploy.template.yaml.
// Walks upward from startPath until a directory containing
// config/deploy.yaml or config/deploy.template.yaml is found (the project
// root), or the filesystem root is reached.
function findConfigFile(startPath: string): {
  deployPath: string | null;
  templatePath: string | null;
  configDir: string | null;
} {
  let currentPath = startPath;
  
  for (;;) {
    const configDir = path.join(currentPath, 'config');
    const deployPath = path.join(configDir, 'deploy.yaml');
    const templatePath = path.join(configDir, 'deploy.template.yaml');
    
    const hasDeploy = fs.existsSync(deployPath);
    const hasTemplate = fs.existsSync(templatePath);
    
    if (hasDeploy || hasTemplate) {
      return {
        deployPath: hasDeploy ? deployPath : null,
        templatePath: hasTemplate ? templatePath : null,
        configDir,
      };
    }
    
    const parentPath = path.dirname(currentPath);
    if (parentPath === currentPath) break;
    currentPath = parentPath;
  }
  
  return { deployPath: null, templatePath: null, configDir: null };
}

/**
 * Load the deploy config into the AppState singleton.
 *
 * On success the startup config and the persistent Webapp.Lang/Theme
 * values are written into appState and appState.configError is cleared.
 * On failure only appState.configError is set; the caller routes to the
 * error page.
 */
export function loadConfig(): void {
  // Start from the directory of the electron binary and walk upward to locate
  // the project root. process.cwd() must not be used: the app may be started
  // by a scheduled task with an unrelated working directory, which would make
  // the config file unfindable.
  const startPath = path.dirname(process.execPath);
  const { deployPath, templatePath, configDir } = findConfigFile(startPath);
  
  // No config files found
  if (!deployPath && !templatePath) {
    appState.configError = {
      type: 'config_not_found',
      message: 'Could not find deploy.yaml or deploy.template.yaml',
      currentPath: startPath,
    };
    return;
  }
  
  // First time setup: only template exists
  const isFirstTimeSetup = !deployPath && !!templatePath;
  
  // Use deploy if exists, otherwise template
  const configFilePath = deployPath || templatePath!;
  const rootPath = path.dirname(path.dirname(configFilePath));
  
  const configContent = fs.readFileSync(configFilePath, 'utf-8');
  const config = yaml.load(configContent) as DeployConfig;
  
  // Get Python executable.
  // No default fallback (e.g. 'python' from PATH): mixing in the system
  // python is not allowed, so a missing config or a missing file is a hard
  // error.
  const pythonExecutableRaw = config.Python?.PythonExecutable;
  if (!pythonExecutableRaw) {
    appState.configError = {
      type: 'python_not_found',
      message: 'Python.PythonExecutable is not configured in deploy.yaml',
      currentPath: startPath,
    };
    return;
  }

  // Resolve relative paths against the project root, never against the
  // process working directory.
  const pythonExecutable = path.isAbsolute(pythonExecutableRaw)
    ? pythonExecutableRaw
    : path.join(rootPath, pythonExecutableRaw);
  
  // Verify Python executable exists
  if (!fs.existsSync(pythonExecutable)) {
    appState.configError = {
      type: 'python_not_found',
      message: `Python executable not found: ${pythonExecutable}`,
      currentPath: startPath,
    };
    return;
  }
  
  // Verify gui.py exists
  const guiPath = path.join(rootPath, 'gui.py');
  if (!fs.existsSync(guiPath)) {
    appState.configError = {
      type: 'guipy_not_found',
      message: `gui.py not found at: ${guiPath}`,
      currentPath: startPath,
    };
    return;
  }
  
  // Success: populate the AppState singleton.
  // The webapp main process (AppState) is the single source of truth for
  // language/theme; deploy.yaml (Webapp.Lang / Webapp.Theme) is only the
  // persistence layer. Defaults stay 'system' when the fields are absent.
  appState.pythonExecutable = pythonExecutable;
  appState.rootPath = rootPath;
  // Command-line args given to gui.py take priority over the Backend
  // section, so the webapp explicitly passes these on startup.
  appState.backendHost = config.Backend?.Host || '0.0.0.0';
  appState.backendPort = config.Backend?.Port || 22267;
  appState.isFirstTimeSetup = isFirstTimeSetup;
  appState.templatePath = templatePath || undefined;
  appState.deployPath = deployPath || path.join(configDir!, 'deploy.yaml');
  const lang = config.Webapp?.Lang;
  if (lang && isConfigLang(lang)) {
    appState.configLang = lang;
  }
  const theme = config.Webapp?.Theme;
  if (theme && isConfigTheme(theme)) {
    appState.configTheme = theme;
  }
  appState.configError = undefined;
}
