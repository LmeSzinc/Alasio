// @vitest-environment node
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:net";
import { isAbsolute, join } from "node:path";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { WebsocketManager } from "./client.svelte";
import type { RequestEvent } from "./event";
import { createRpc } from "./rpc.svelte";

// Rpc failures show toasts through svelte-sonner; keep them out of the
// node test environment.
vi.mock("svelte-sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.setConfig({ hookTimeout: 120_000, testTimeout: 20_000 });

/**
 * End-to-end smoke test: the real frontend ws stack (WebsocketManager +
 * createRpc) talking to the real python backend through a real
 * WebSocket connection.
 *
 * The backend is started the same way as the desktop app does it:
 * `python gui.py --host 127.0.0.1 --port <port>` (supervisor mode),
 * and stopped gracefully through the stdin `command:stop` contract.
 *
 * The project root is located by walking up from this file until
 * `config/deploy.yaml` is found; the python interpreter is read from
 * that file (Python.PythonExecutable), overridable with the
 * ALASIO_PYTHON env var. The suite is skipped entirely when no
 * interpreter is available.
 */

/**
 * Walks upward from `startPath` until a directory containing
 * config/deploy.yaml (or config/deploy.template.yaml) is found — that
 * directory is the project root. Mirrors webapp/main/config.ts
 * findConfigFile().
 *
 * Args:
 *     startPath (str): Directory to start the walk from
 *
 * Returns:
 *     tuple[str, str]: (config file path, project root directory)
 */
function findDeployConfig(startPath: string): { configPath: string; rootPath: string } {
  let currentPath = startPath;
  for (;;) {
    const deployPath = join(currentPath, "config", "deploy.yaml");
    const templatePath = join(currentPath, "config", "deploy.template.yaml");
    if (existsSync(deployPath)) return { configPath: deployPath, rootPath: currentPath };
    if (existsSync(templatePath)) return { configPath: templatePath, rootPath: currentPath };
    const parentPath = dirname(currentPath);
    if (parentPath === currentPath) break;
    currentPath = parentPath;
  }
  throw new Error(`config/deploy.yaml not found while walking up from ${startPath}`);
}

/**
 * Reads the Python.PythonExecutable value from the deploy config file.
 * The project's deploy.yaml is a small hand-edited YAML subset; only
 * the line `PythonExecutable: <path>` is needed here (the webapp parses
 * the full YAML with the yaml package, which tests do not depend on).
 *
 * Args:
 *     configPath (str): Absolute path of config/deploy.yaml
 *
 * Returns:
 *     str | None: The configured python executable, or None if absent
 */
function readPythonExecutable(configPath: string): string | undefined {
  const content = readFileSync(configPath, "utf8");
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    if (trimmed.startsWith("PythonExecutable:")) {
      return trimmed
        .slice("PythonExecutable:".length)
        .trim()
        .replace(/^['"]|['"]$/g, "");
    }
  }
  return undefined;
}

const { configPath: DEPLOY_CONFIG, rootPath: REPO_ROOT } = findDeployConfig(
  fileURLToPath(new URL(".", import.meta.url)),
);

/**
 * Resolves the python interpreter: ALASIO_PYTHON env var first, then
 * Python.PythonExecutable from the deploy config. Relative paths are
 * resolved against the project root. Returns undefined when nothing
 * resolves to an existing file.
 *
 * Returns:
 *     str | None: Absolute path of an existing python executable
 */
function resolvePython(): string | undefined {
  const configured = process.env.ALASIO_PYTHON;
  const candidates = [configured, readPythonExecutable(DEPLOY_CONFIG)];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const resolved = isAbsolute(candidate) ? candidate : join(REPO_ROOT, candidate);
    if (existsSync(resolved)) return resolved;
  }
  return undefined;
}

const pythonPath = resolvePython();
const describeE2e = pythonPath ? describe : describe.skip;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The backend now enforces the electron layer: the
 * deployment has no password configured, so the global gate (rule A)
 * only lets requests carrying a valid electron token through. The test
 * backend is therefore spawned with the host pid in the environment and the
 * token announced
 * on stdout is injected into the ws handshake header.
 */
class HeaderedWebSocket extends globalThis.WebSocket {
  constructor(url: string) {
    // undici (node's built-in WebSocket) accepts a headers option in the
    // second argument; the token plays the role the Electron webRequest
    // injection plays in the real client.
    super(url, { headers: { "X-Alasio-Token": backendToken } } as any);
  }
}

let backendToken = "";

async function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address && typeof address === "object") {
        const port = address.port;
        server.close(() => resolve(port));
      } else {
        server.close(() => reject(new Error("failed to resolve a free port")));
      }
    });
  });
}

async function waitFor(
  predicate: () => boolean,
  timeoutMs: number,
  label = "condition",
  intervalMs = 50,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await sleep(intervalMs);
  }
  throw new Error(`waitFor "${label}" timed out after ${timeoutMs}ms`);
}

/**
 * Spawns the backend through gui.py (same entry as the desktop app, with the
 * host pid in the environment so the token announcements and the electron
 * layer are exercised) and resolves once hypercorn reports "Running on
 * http://...". The output listeners stay attached so the pipe cannot fill up.
 */
function startBackend(python: string, port: number): Promise<ChildProcess> {
  return new Promise((resolve, reject) => {
    const proc = spawn(python, ["gui.py", "--host", "127.0.0.1", "--port", String(port)], {
      cwd: REPO_ROOT,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1", ALASIO_APP_ELECTRON_PID: String(process.pid) },
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      fn();
    };

    const onData = (stream: "stdout" | "stderr") => (data: Buffer) => {
      const text = data.toString();
      if (stream === "stdout") {
        stdout += text;
        // parse the chained token announcement (the initial begin form)
        const match = /\[Supervisor\] token_set:begin:([0-9a-f]{64})/.exec(stdout);
        if (match) backendToken = match[1];
      } else {
        stderr += text;
      }
      if (text.includes("Running on http")) {
        settle(() => resolve(proc));
      }
    };
    proc.stdout?.on("data", onData("stdout"));
    proc.stderr?.on("data", onData("stderr"));
    proc.on("error", (err) => settle(() => reject(err)));
    proc.on("exit", (code) => {
      settle(() =>
        reject(new Error(`backend exited before ready (code=${code})\nstdout: ${stdout}\nstderr: ${stderr}`)),
      );
    });
    setTimeout(() => {
      settle(() => reject(new Error(`backend startup timed out\nstdout: ${stdout}\nstderr: ${stderr}`)));
    }, 60_000);
  });
}

/** Stops the backend gracefully via stdin, then force-kills on timeout. */
async function stopBackend(proc: ChildProcess | undefined): Promise<void> {
  if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;

  const exited = new Promise<void>((resolve) => proc.once("exit", () => resolve()));
  try {
    proc.stdin?.write("command:stop\n");
  } catch {
    // stdin already closed; fall through to force kill
  }

  if (await Promise.race([exited.then(() => true), sleep(8000).then(() => false)])) return;

  // Graceful shutdown failed: kill the whole process tree.
  if (process.platform === "win32") {
    await new Promise<void>((resolve) => {
      const killer = spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"], { windowsHide: true });
      killer.on("exit", () => resolve());
      killer.on("error", () => resolve());
    });
  } else {
    try {
      process.kill(-proc.pid!, "SIGKILL");
    } catch {
      // already gone
    }
  }
  await Promise.race([exited, sleep(3000)]);
}

/** Client subclass pinned to the test backend's url. */
class TestClient extends WebsocketManager {
  constructor(private readonly url: string) {
    super();
  }
  protected override getWsUrl(): string {
    return this.url;
  }
}

/** A promise-based rpc helper: resolves with the raw response. */
function callRpc(
  client: WebsocketManager,
  func: string,
  args: Record<string, any>,
): Promise<{ success: boolean; value: string }> {
  return new Promise((resolve) => {
    const rpc = createRpc("ConnState", client, { pendingDelay: 0, timeout: 10_000 });
    rpc.call(func, args, {
      onSuccess: (id) => resolve({ success: true, value: id }),
      onError: (message) => resolve({ success: false, value: message }),
    });
  });
}

describeE2e("TestBackendE2e", () => {
  let proc: ChildProcess | undefined;
  let port: number;
  let client: TestClient;

  beforeAll(async () => {
    // Any failure below (startup, connection) fails the suite: the
    // python interpreter exists but the backend did not come up, which
    // is an environment problem worth surfacing. When the interpreter
    // is missing entirely, describeE2e skips this file up front.
    port = await getFreePort();
    proc = await startBackend(pythonPath!, port);

    client = new TestClient(`ws://127.0.0.1:${port}/api/ws`);
    expect(backendToken).toMatch(/^[0-9a-f]{64}$/);
    vi.stubGlobal("WebSocket", HeaderedWebSocket);
    client.connect();
    await waitFor(() => client.connectionState === "open", 15_000, "connection open");
  });

  afterAll(async () => {
    await stopBackend(proc);
  });

  it("connects with the default ConnState subscription active", () => {
    expect(client.connectionState).toBe("open");
    expect(client.connectionGeneration).toBeGreaterThan(0);
    expect(client.topicReady).toEqual({ ConnState: true });
    // ConnState is a rpc-only topic (no `data` implementation): default
    // subscriptions are registered server-side but never push data.
    expect(client.topics["ConnState"]).toBeUndefined();
  });

  it("subscribes to ConfigScan and receives the full config list", async () => {
    client.sub("ConfigScan");
    // The full snapshot is a dict of config_name -> config info.
    await waitFor(() => client.topics["ConfigScan"] !== undefined, 15_000, "ConfigScan full");
    expect(typeof client.topics["ConfigScan"]).toBe("object");
    expect(Object.keys(client.topics["ConfigScan"])).not.toHaveLength(0);
    expect(client.subscriptions["ConfigScan"]).toBe(1);
  });

  it("answers a successful rpc without an error value", async () => {
    // set_nav("") is a no-op on a fresh connection (nav_name is already
    // empty), so the call succeeds without mutating any state.
    const result = await callRpc(client, "set_nav", { name: "" });
    expect(result.success).toBe(true);
    expect(result.value.length).toBeGreaterThan(0);
  });

  it("reports an unknown rpc method as an error string", async () => {
    const result = await callRpc(client, "no_such_method", {});
    expect(result.success).toBe(false);
    expect(result.value.length).toBeGreaterThan(0);
  });
});
