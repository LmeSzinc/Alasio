import { ChildProcess, spawn } from "child_process";
import { BrowserWindow, session } from "electron";
import kill from "tree-kill";
import { IPC_BACKEND_LOG, IPC_BACKEND_READY } from "../shared/ipc";
import { acceptAnnouncement, parseAnnouncement } from "./announcement";
import { appState } from "./app-state";
import { LineSplitter } from "./line-splitter";
import { StartupLogBuffer } from "./startup-log";

// Backend startup status callback, wired by main/index.ts to shared state
// (setBackendStatus). Injected instead of imported to break a circular
// import chain (shared-state -> app-state -> backend -> shared-state):
// rolldown would otherwise emit shared-state before app-state, crashing
// shared-state's module-body appState.onChange() registration on an
// undefined appState.
let onBackendStatus: ((status: "starting" | "success" | "failed") => void) | null = null;

/**
 * Wire the backend startup status callback. Called once by main/index.ts
 * at module scope (after every module finished loading), before any
 * startBackend() call.
 *
 * Args:
 *     callback (function): Receives "starting" at the beginning of every
 *         launch attempt, "success" when an attempt settles without
 *         error, "failed" when one settles with an error
 */
export function setBackendStatusCallback(callback: (status: "starting" | "success" | "failed") => void) {
  onBackendStatus = callback;
}

export enum ShutdownStage {
  WaitingGraceful = "waiting",
  ForcingGraceful = "forcing",
  Killing = "killing",
  Done = "done",
}

// Timeout for backend startup. The backend imports heavy modules (alasio,
// hypercorn, trio) and spawns a multiprocessing child process, which can take
// a while on slow machines.
const BACKEND_START_TIMEOUT = 30_000;

let backendProcess: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

// Electron-layer token state:
// - authToken: the currently accepted token from the supervisor's stdout
//   announcements. Token lifetime = supervisor lifetime: cleared on every
//   child exit, before any new spawn.
// - backendReady: stderr observed hypercorn's "Running on http".
// The app UI is only released (maybeOpenApp) when BOTH conditions hold:
// authToken non-null means an announcement was received from the CURRENT
// alive supervisor, so the injected token is guaranteed to already be in
// the new backend's token table (seeded through spawn args before serve).
let authToken: string | null = null;
let backendReady = false;

// Startup log buffer (main side of the loading page log channel): the
// backend starts streaming stdout/stderr right after spawn, while the
// loading page only registers its listener after the renderer mounted.
// Lines produced before the subscription are buffered here (bounded,
// chronological) and replayed in full when the page subscribes, because
// webContents.send does not queue for unregistered listeners (see
// startup-log.ts and doc/2026-09-03_electron-window-flicker-and-loading-page.md
// §4.2).
const startupLog = new StartupLogBuffer();

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function setMainWindow(window: BrowserWindow) {
  mainWindow = window;
}

/**
 * Record one startup stdout/stderr line. Before the backend is ready the
 * line is buffered (bounded, chronological) and additionally pushed to
 * the window once a renderer subscribed (subscribeBackendLogs); after
 * ready no line is recorded anymore, matching the previous "stop
 * forwarding after ready" behavior so the buffer stops growing.
 *
 * Args:
 *     line (str): One complete log line, without trailing newline
 */
function recordStartupLog(line: string) {
  if (backendReady) return;
  startupLog.record(line);
  if (startupLog.isSubscribed) {
    mainWindow?.webContents.send(IPC_BACKEND_LOG, line);
  }
}

/**
 * Subscribe to the startup log stream. Marks the channel as subscribed
 * (from this point recordStartupLog pushes every new line through
 * IPC_BACKEND_LOG in addition to buffering it) and returns the current
 * buffer so the caller can replay everything produced before the
 * subscription. Registered as the IPC_BACKEND_LOG_SUBSCRIBE invoke
 * handler by main/index.ts.
 *
 * Returns:
 *     list[str]: Buffered lines in chronological order (oldest first);
 *         the renderer reverses the snapshot for its newest-first view
 */
export function subscribeBackendLogs(): string[] {
  return startupLog.subscribe();
}

/**
 * Register the webRequest token injection for http:// and ws:// requests
 * to the local backend. Must be called once after app
 * ready; the callback closure reads the live authToken variable, so the
 * announcement parser only needs to update the variable, no re-registration.
 *
 * The host:port match covers both http:// and ws:// (Electron 22's
 * onBeforeSendHeaders intercepts WebSocket upgrade handshakes, Phase 0
 * spike verified). authToken is null while the backend is not ready or
 * already exited: nothing is injected.
 */
export function registerTokenInjection(): void {
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    try {
      const url = new URL(details.url);
      const local = url.hostname === "127.0.0.1" && url.port === String(appState.backendPort);
      if (local && authToken) {
        callback({ requestHeaders: { ...details.requestHeaders, "X-Alasio-Token": authToken } });
      } else {
        callback({ requestHeaders: details.requestHeaders });
      }
    } catch {
      // unparsable url, pass through
      callback({ requestHeaders: details.requestHeaders });
    }
  });
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
    console.error("Failed to write stdin command:", err);
    return false;
  }
}

export function startBackend(
  pythonExecutable: string,
  rootPath: string,
  backendHost: string,
  backendPort: number,
): Promise<void> {
  return new Promise((resolve, reject) => {
    // Every launch attempt starts with a clean status and a clean log:
    // back to "starting" and an empty buffer, so a retry neither shows
    // the previous failure hint nor mixes the previous attempt's lines
    // into the new one. The renderer derives its failure hint from
    // shared state, so the retry UI switches to the running state before
    // the new process spawns.
    onBackendStatus?.("starting");
    startupLog.clear();

    // gui.py forwards sys.argv to the backend supervisor, which passes them
    // down to the hypercorn config parser (--host/--port in create_config).
    // --host/--port must be passed explicitly: command-line args take
    // priority over the deploy.yaml Backend section, and without --port the
    // backend would fall back to hypercorn's default 8000.
    // --electron switches the supervisor into electron mode: it generates /
    // rotates / announces the token and sets ELECTRON=1 for the whole
    // backend chain. Without it no token exists and sensitive APIs stay
    // locked (403).
    const child = spawn(
      pythonExecutable,
      ["gui.py", "--host", backendHost, "--port", String(backendPort), "--electron"],
      {
        cwd: rootPath,
        // stdin is piped so graceful shutdown can be requested through it
        stdio: ["pipe", "pipe", "pipe"],
        // python block-buffers stdout on a pipe; unbuffered mode makes the
        // token announcements (and supervisor logs) reach us immediately
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      },
    );
    backendProcess = child;

    let settled = false;

    const settle = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) {
        // Clean up the process on startup failure so no orphan python remains
        if (child.exitCode === null && child.pid) {
          kill(child.pid, "SIGKILL", () => {});
        }
        // Publish the startup status through shared state: a fast failure
        // (spawn error, missing gui.py...) can happen before the renderer
        // mounted, and shared state is read synchronously at renderer
        // start, so the failure hint is never lost to an event race.
        // The log buffer is deliberately NOT cleared: the error tail must
        // stay replayable (it is dropped by the next attempt's clear).
        onBackendStatus?.("failed");
        reject(error);
      } else {
        // Startup succeeded: publish it (the renderer navigates to the
        // app page right after, but the shared state stays correct even
        // if the navigation races with the renderer's read) and drop the
        // buffer - the loading page is about to leave, successful logs no
        // longer need to be replayed and must not stay in memory.
        onBackendStatus?.("success");
        startupLog.clear();
        resolve();
      }
    };

    // Fallback in case the ready message is never observed
    const timeout = setTimeout(() => {
      settle(new Error(`Backend startup timed out after ${BACKEND_START_TIMEOUT} ms`));
    }, BACKEND_START_TIMEOUT);

    /**
     * Release the app UI only when the gate holds: a token from the current
     * supervisor AND hypercorn up. authToken non-null ⇔ an announcement of
     * the currently alive supervisor was received (exit clears it), and the
     * announcement is emitted before the backend spawn, so by the time
     * "Running on http" arrives the token is already seeded into the table.
     */
    const maybeOpenApp = () => {
      if (settled) return;
      if (authToken && backendReady) {
        settle();
        mainWindow?.webContents.send(IPC_BACKEND_READY);
        // Backend is up: push the current language/theme so the backend
        // persists them into deploy.yaml (idempotent: no write when the
        // value already matches).
        appState.broadcastPrefs();
      }
    };

    // stdout: line-buffered token announcement parsing. The stream mixes
    // supervisor mprint output and (before ready) backend prints; data
    // chunks may split lines, so LineSplitter accumulates and splits on
    // newlines. Every complete line goes through parseAnnouncement first
    // (chained validation); announcement lines are never forwarded to the
    // renderer (the begin announcement carries the electron token, which
    // must never enter the renderer JS environment). The forward filter
    // is line-based, so a token line split across chunks is filtered too.
    const stdoutLines = new LineSplitter((line) => {
      const announcement = parseAnnouncement(line);
      if (!announcement) {
        // non-announcement lines go to the loading page log (before ready).
        // LineSplitter already stripped the trailing newline; the renderer
        // displays each message as its own block-level line, so no "\n"
        // needs to be re-appended. recordStartupLog buffers the line and
        // pushes it once the loading page subscribed (no-op after ready).
        recordStartupLog(line);
        return;
      }
      const next = acceptAnnouncement(authToken, announcement.old, announcement.next);
      if (next) {
        authToken = next;
        maybeOpenApp();
      }
    });
    child.stdout?.on("data", (data: Buffer) => {
      stdoutLines.push(data.toString());
    });

    // stderr: hypercorn prints "Running on http://..." here (supervisor
    // logs also land here before ready). Line-split like stdout so every
    // message is buffered/pushed as a whole line; ready detection runs on
    // the complete line ("Running on http" split across chunks can no
    // longer be missed or matched on a partial line).
    const stderrLines = new LineSplitter((line) => {
      // recordStartupLog is a no-op once the backend is ready; the ready
      // detection below must observe the "Running on http" line BEFORE
      // that flag flips, so the recording happens first.
      recordStartupLog(line);
      if (!backendReady && line.includes("Running on http")) {
        backendReady = true;
        maybeOpenApp();
      }
    });
    child.stderr?.on("data", (data: Buffer) => {
      stderrLines.push(data.toString());
    });

    child.on("error", (err) => settle(err));

    child.on("exit", (code) => {
      // Token lifetime = supervisor lifetime: clear unconditionally, and
      // BEFORE any new spawn (automatic respawn happens in the exit
      // callback of the old process). Even a fully ready backend loses its
      // token on exit; the next supervisor announces a fresh one. Only
      // clear when this child is still the current backend: a stale exit
      // event from a previous spawn (retry raced with the kill) must not
      // wipe the state of the newly spawned backend.
      if (backendProcess === child) {
        authToken = null;
        backendReady = false;
        backendProcess = null;
      }
      if (!settled) {
        settle(new Error(`Backend exited before ready (code: ${code})`));
      }
    });
  });
}

export async function shutdownBackend(onStageChange?: (stage: ShutdownStage) => void): Promise<void> {
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

  backendProcess.once("exit", () => {
    exited = true;
  });

  // Send a graceful stop command through stdin. The python supervisor reads
  // stdin in a background thread and forwards "command:stop" to the backend,
  // which then shuts down gracefully; unknown stdin input is silently dropped.
  const sendStop = () => {
    try {
      backendProcess?.stdin?.write("command:stop\n");
    } catch (err) {
      // stdin is already closed, the process is exiting or already gone
      console.error("Failed to send stop command:", err);
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
    kill(pid, "SIGKILL", (err) => {
      if (err) console.error("tree-kill error:", err);
      resolve();
    });
  });

  // tree-kill terminates the process, wait briefly for the exit event
  await waitForExit(500);
  onStageChange?.(ShutdownStage.Done);
}
