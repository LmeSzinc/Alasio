import * as path from 'path';
import * as fs from 'fs';
import * as yaml from 'js-yaml';

interface DeployConfig {
  Python?: {
    PythonExecutable?: string;
  };
  Webui?: {
    Language?: string;
  };
  Backend?: {
    Host?: string;
    Port?: number;
  };
}

export interface AppConfig {
  pythonExecutable: string;
  language: string;
  backendHost: string;
  backendPort: number;
  rootPath: string;
  isFirstTimeSetup: boolean;
  templatePath?: string;
  deployPath?: string;
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

export function loadConfig(): AppConfig | ConfigError {
  // Start from the directory of the electron binary and walk upward to locate
  // the project root. process.cwd() must not be used: the app may be started
  // by a scheduled task with an unrelated working directory, which would make
  // the config file unfindable.
  const startPath = path.dirname(process.execPath);
  const { deployPath, templatePath, configDir } = findConfigFile(startPath);
  
  // No config files found
  if (!deployPath && !templatePath) {
    return {
      type: 'config_not_found',
      message: 'Could not find deploy.yaml or deploy.template.yaml',
      currentPath: startPath,
    };
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
    return {
      type: 'python_not_found',
      message: 'Python.PythonExecutable is not configured in deploy.yaml',
      currentPath: startPath,
    };
  }

  // Resolve relative paths against the project root, never against the
  // process working directory.
  const pythonExecutable = path.isAbsolute(pythonExecutableRaw)
    ? pythonExecutableRaw
    : path.join(rootPath, pythonExecutableRaw);
  
  // Verify Python executable exists
  if (!fs.existsSync(pythonExecutable)) {
    return {
      type: 'python_not_found',
      message: `Python executable not found: ${pythonExecutable}`,
      currentPath: startPath,
    };
  }
  
  // Verify gui.py exists
  const guiPath = path.join(rootPath, 'gui.py');
  if (!fs.existsSync(guiPath)) {
    return {
      type: 'guipy_not_found',
      message: `gui.py not found at: ${guiPath}`,
      currentPath: startPath,
    };
  }
  
  return {
    pythonExecutable,
    language: config.Webui?.Language || '',
    // Command-line args given to gui.py take priority over the Backend
    // section, so the webapp explicitly passes these on startup.
    backendHost: config.Backend?.Host || '0.0.0.0',
    backendPort: config.Backend?.Port || 22267,
    rootPath,
    isFirstTimeSetup,
    templatePath: templatePath || undefined,
    deployPath: deployPath || path.join(configDir!, 'deploy.yaml'),
  };
}

export async function saveFirstTimeConfig(
  templatePath: string,
  deployPath: string,
  language: string
): Promise<void> {
  const templateContent = fs.readFileSync(templatePath, 'utf-8');
  const config = yaml.load(templateContent) as DeployConfig;
  
  // Update language
  if (!config.Webui) config.Webui = {};
  config.Webui.Language = language;
  
  // Write to deploy.yaml
  const newContent = yaml.dump(config, {
    indent: 2,
    lineWidth: -1,
  });
  
  fs.writeFileSync(deployPath, newContent, 'utf-8');
}
