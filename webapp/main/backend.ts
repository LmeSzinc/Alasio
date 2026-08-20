import { ChildProcess, spawn } from 'child_process';
import { BrowserWindow } from 'electron';
import { IPC_BACKEND_LOG, IPC_BACKEND_READY } from '../shared/ipc';
import { appState } from './app-state';
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

/**
 * Write one command line to the backend stdin (e.g. the stdin contract
 * commands command:set_lang:{lang} / command:set_theme:{theme}). The
 * supervisor forwards recognized commands to the backend process.
 *
 * Returns:
 *     bool: True if the command was written, False if the backend is not
 *     running (never started, already exited or stdin unavailable)
 */
export function sendStdinCommand(command: string): boolean {
  if (
    !backendProcess ||
    !backendProcess.stdin ||
    !backendProcess.pid ||
    backendProcess.exitCode !== null ||
    backendProcess.signalCode !== null
  ) {
    return false;
  }
  try {
    backendProcess.stdin.write(`${command}\n`);
    return true;
  } catch (err) {
    // stdin is already closed, the process is exiting or already gone
    console.error('Failed to write stdin command:', err);
    return false;
  }
}

export function startBackend(
  pythonExecutable: string,
  rootPath: string,
  backendHost: string,
  backendPort: number
): Promise<void> {
  return new Promise((resolve, reject) => {
    // gui.py forwards sys.argv to the backend supervisor, which passes them
    // down to the hypercorn config parser (--host/--port in create_config).
    // --host/--port must be passed explicitly: command-line args take
    // priority over the deploy.yaml Backend section, and without --port the
    // backend would fall back to hypercorn's default 8000.
    const child = spawn(
      pythonExecutable,
      ['gui.py', '--host', backendHost, '--port', String(backendPort)],
      {
        cwd: rootPath,
        // stdin is piped so graceful shutdown can be requested through it
        stdio: ['pipe', 'pipe', 'pipe'],
      }
    );
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
        // Backend is up: push the current language/theme so the backend
        // persists them into deploy.yaml (idempotent: no write when the
        // value already matches).
        appState.broadcastPrefs();
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

  // Send a graceful stop command through stdin. The python supervisor reads
  // stdin in a background thread and forwards "command:stop" to the backend,
  // which then shuts down gracefully; unknown stdin input is silently dropped.
  const sendStop = () => {
    try {
      backendProcess?.stdin?.write('command:stop\n');
    } catch (err) {
      // stdin is already closed, the process is exiting or already gone
      console.error('Failed to send stop command:', err);
    }
  };

  // Poll for the exit event in short intervals instead of sleeping the full
  // timeout, so each stage finishes as soon as the process is gone.
  const waitForExit = async (timeoutMs: number): Promise<boolean> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (exited) return true;
      await sleep(50);
    }
    return exited;
  };

  // Stage 1: Send stop command (0s)
  onStageChange?.(ShutdownStage.WaitingGraceful);
  sendStop();

  if (await waitForExit(2000)) {
    onStageChange?.(ShutdownStage.Done);
    return;
  }

  // Stage 2: Send stop command again (2s)
  onStageChange?.(ShutdownStage.ForcingGraceful);
  sendStop();

  if (await waitForExit(2000)) {
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

  // tree-kill terminates the process, wait briefly for the exit event
  await waitForExit(500);
  onStageChange?.(ShutdownStage.Done);
}