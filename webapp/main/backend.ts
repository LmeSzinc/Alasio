import { ChildProcess, spawn } from 'child_process';
import { BrowserWindow } from 'electron';
import { IPC_BACKEND_LOG, IPC_BACKEND_READY } from '../shared/ipc';
import kill from 'tree-kill';

export enum ShutdownStage {
  WaitingGraceful = 'waiting',
  ForcingGraceful = 'forcing',
  Killing = 'killing',
  Done = 'done'
}

// Timeout for backend startup. The backend imports heavy modules (alasio,
// hypercorn, trio) and spawns a multiprocessing child process, which can take
// a while on slow machines.
const BACKEND_START_TIMEOUT = 30_000;

let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

export function startBackend(
  pythonExecutable: string,
  rootPath: string,
  webuiPort: number
): Promise<void> {
  return new Promise((resolve, reject) => {
    // gui.py forwards sys.argv to the backend supervisor, which passes them
    // down to the hypercorn config parser (--host/--port in create_config).
    // Without --port the backend would listen on hypercorn's default 8000
    // instead of the configured webuiPort.
    const child = spawn(pythonExecutable, ['gui.py', '--port', String(webuiPort)], {
      cwd: rootPath,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    backendProcess = child;

    let isReady = false;
    let settled = false;

    const settle = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) {
        // Clean up the process on startup failure so no orphan python remains
        if (child.exitCode === null && child.pid) {
          kill(child.pid, 'SIGKILL', () => {});
        }
        reject(error);
      } else {
        resolve();
      }
    };

    // Fallback in case the ready message is never observed
    const timeout = setTimeout(() => {
      settle(new Error(`Backend startup timed out after ${BACKEND_START_TIMEOUT} ms`));
    }, BACKEND_START_TIMEOUT);

    // Forward logs to the renderer and watch for hypercorn's ready message.
    // The supervisor prints "[Supervisor] Running on PID: xxx" to stdout before
    // the backend subprocess is even spawned, so we match the exact hypercorn
    // message "Running on http://..." (printed to stderr) instead of a plain
    // "Running on". Both streams are watched in case hypercorn logging moves.
    const handleOutput = (data: Buffer) => {
      const text = data.toString();

      // Only push logs before backend is ready (prevent memory growth)
      if (isReady) return;
      mainWindow?.webContents.send(IPC_BACKEND_LOG, text);

      if (text.includes('Running on http')) {
        isReady = true;
        mainWindow?.webContents.send(IPC_BACKEND_READY);
        settle();
      }
    };

    child.stdout?.on('data', handleOutput);
    child.stderr?.on('data', handleOutput);

    child.on('error', (err) => settle(err));

    child.on('exit', (code) => {
      if (!isReady) {
        settle(new Error(`Backend exited before ready (code: ${code})`));
      }
    });
  });
}

export async function shutdownBackend(
  onStageChange?: (stage: ShutdownStage) => void
): Promise<void> {
  // If backend was never started or has already exited, mark shutdown success immediately.
  // signalCode is set when the process was terminated by a signal (exitCode stays null).
  if (
    !backendProcess ||
    !backendProcess.pid ||
    backendProcess.exitCode !== null ||
    backendProcess.signalCode !== null
  ) {
    onStageChange?.(ShutdownStage.Done);
    return;
  }

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