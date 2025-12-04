import * as path from 'path';
import * as fs from 'fs';
import * as yaml from 'js-yaml';

interface DeployConfig {
  Python?: {
    PythonExecutable?: string;
  };
  Webui?: {
    Language?: string;
    WebuiPort?: number;
  };
}

export interface AppConfig {
  pythonExecutable: string;
  language: string;
  webuiPort: number;
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

// Search for deploy.yaml or deploy.template.yaml
function findConfigFile(startPath: string, maxLevels: number = 3): {
  deployPath: string | null;
  templatePath: string | null;
  configDir: string | null;
} {
  let currentPath = startPath;
  
  for (let i = 0; i < maxLevels; i++) {
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
  const startPath = path.join(__dirname, '../..');
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
  
  // Get Python executable
  let pythonExecutable = config.Python?.PythonExecutable || 'python';
  if (!path.isAbsolute(pythonExecutable)) {
    pythonExecutable = path.join(rootPath, pythonExecutable);
  }
  
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
    webuiPort: config.Webui?.WebuiPort || 22267,
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
