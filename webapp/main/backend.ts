import { ChildProcess, spawn } from 'child_process';
import { BrowserWindow } from 'electron';
import kill from 'tree-kill';

export enum ShutdownStage {
  WaitingGraceful = 'waiting',
  ForcingGraceful = 'forcing',
  Killing = 'killing',
  Done = 'done'
}

let backendProcess: ChildProcess | null = null;
let isBackendReady = false;
let mainWindow: BrowserWindow | null = null;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function startBackend(
  pythonExecutable: string,
  rootPath: string
): Promise<void> {
  return new Promise((resolve, reject) => {
    backendProcess = spawn(pythonExecutable, ['gui.py'], {
      cwd: rootPath,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    backendProcess.stdout?.on('data', (data) => {
      const text = data.toString();
      
      // Only push logs before backend is ready (prevent memory growth)
      if (!isBackendReady) {
        mainWindow?.webContents.send('backend:log', text);
      }
      
      // Check for startup completion signal
      if (text.includes('Running on')) {
        isBackendReady = true;
        mainWindow?.webContents.send('backend:ready');
        resolve();
      }
    });

    backendProcess.stderr?.on('data', (data) => {
      const text = data.toString();
      if (!isBackendReady) {
        mainWindow?.webContents.send('backend:log', text);
      }
    });

    backendProcess.on('error', (err) => {
      reject(err);
    });

    backendProcess.on('exit', (code) => {
      mainWindow?.webContents.send('backend:exit', code);
    });
  });
}

export async function shutdownBackend(
  onStageChange?: (stage: ShutdownStage) => void
): Promise<void> {
  if (!backendProcess || !backendProcess.pid) return;

  const pid = backendProcess.pid;
  let exited = false;

  backendProcess.once('exit', () => {
    exited = true;
  });

  // Stage 1: Send SIGINT (0s)
  onStageChange?.(ShutdownStage.WaitingGraceful);
  backendProcess.kill('SIGINT');

  await sleep(2000);
  if (exited) {
    onStageChange?.(ShutdownStage.Done);
    return;
  }

  // Stage 2: Send SIGINT again (2s)
  onStageChange?.(ShutdownStage.ForcingGraceful);
  backendProcess.kill('SIGINT');

  await sleep(2000);
  if (exited) {
    onStageChange?.(ShutdownStage.Done);
    return;
  }

  // Stage 3: tree-kill (4s)
  onStageChange?.(ShutdownStage.Killing);
  await new Promise<void>((resolve) => {
    kill(pid, 'SIGKILL', (err) => {
      if (err) console.error('tree-kill error:', err);
      resolve();
    });
  });

  await sleep(500);
  onStageChange?.(ShutdownStage.Done);
}
