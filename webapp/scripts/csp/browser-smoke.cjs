"use strict";
// Browser smoke test for the Content-Security-Policy, in the real engine.
//
// The static gates (verify.ts / verify.js / verify-asar.ts) compare the built
// page with the hashes it needs, but they are our own model of the browser. A
// policy can be wrong in ways a model does not see: the meta may be dropped,
// the directive may be overridden, the hash variant may be the other one. The
// only proof is the engine itself, so this script loads the real build in
// electron (Chromium 108, the runtime the app ships) and reads the result from
// the live document.
//
// It runs three phases, and the negative ones are what make the positive one
// mean anything:
//
//   1. the real build: no violation reported, the pre-paint theme script ran,
//      the sveltekit bootstrap script ran and the app mounted;
//   2. the template script's hash removed: the engine must report a violation
//      and block it, while the bootstrap script still runs — proof that the
//      policy is enforced per script and not just absent;
//   3. the bootstrap script's hash removed: the app must NOT boot — the exact
//      failure this policy guards against ("CSP 不匹配, 页面加载不出来").
//
// If phase 2 or 3 shows no violation, the policy is not being enforced at all
// and the script fails: the positive phase would otherwise be vacuous.
//
// Targets:
//   --target renderer  the electron app bundle, served over app:// by the
//                      production protocol module (default)
//   --target frontend  the python-backend web ui, served over http with the
//                      CSP header the backend derives from the page meta
//
// Requires a prior `vite build` of the target. Run with:
//   pnpm run smoke:csp              (webapp)
//   pnpm run smoke:csp              (frontend, uses the electron of webapp)
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const Module = require("node:module");
const ts = require("typescript");
const { app, BrowserWindow, ipcMain } = require("electron");

const webappRoot = path.resolve(__dirname, "..", "..");
const repoRoot = path.resolve(webappRoot, "..");
// The page needs time for its dynamic imports (entry chunk, route chunk) to
// resolve and mount; load alone does not mean the app is up.
const MOUNT_DELAY_MS = 1500;
const PHASE_TIMEOUT_MS = 30000;

/**
 * Parse `--name value` and `--name=value` arguments.
 *
 * @param {string[]} argv Arguments after the script path
 * @returns {Record<string, string>} Options by name
 */
function parseArgs(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith("--")) continue;
    const separator = arg.indexOf("=");
    if (separator !== -1) {
      out[arg.slice(2, separator)] = arg.slice(separator + 1);
    } else {
      out[arg.slice(2)] = argv[index + 1] ?? "";
      index += 1;
    }
  }
  return out;
}

/**
 * Load a TypeScript module as CommonJS, the way the build does.
 *
 * The modules used here are single-file and import only node builtins, so a
 * plain transpile is enough and no bundling is involved.
 *
 * @param {string} file Absolute path of the .ts module
 * @returns {Record<string, any>} Module exports
 */
function loadTypeScriptModule(file) {
  const source = fs.readFileSync(file, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: file,
  });
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(path.dirname(file));
  mod._compile(outputText, file);
  return mod.exports;
}

const csp = loadTypeScriptModule(path.join(__dirname, "inline-scripts.ts"));

/**
 * Remove hash sources from every CSP meta and from the policy string.
 *
 * @param {string} html Built page
 * @param {string[]} hashes Hash sources without quotes
 * @returns {string} The page with those hashes stripped
 */
function stripHashes(html, hashes) {
  let out = html;
  for (const hash of hashes) {
    out = out.split(`'${hash}'`).join("");
  }
  return out;
}

/**
 * The CSP value the python backend derives from a page, mirrored here so the
 * http target reproduces the production meta + header intersection.
 *
 * @param {string} html Page being served
 * @returns {string} Value of the Content-Security-Policy header
 */
function headerPolicy(html) {
  const meta = csp.cspMetaContent(html);
  return meta ? `${meta}; frame-ancestors 'self'` : "default-src 'self'";
}

const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

/**
 * The probe evaluated in the page, at dom-ready and after load.
 *
 * `svelteGlobals` is the authoritative "the bootstrap inline script ran"
 * signal: the global is assigned by that script, which the policy allows by
 * hash. `nodes` counts what the SPA rendered.
 */
const PROBE = `(() => ({
  htmlClass: document.documentElement.className,
  colorScheme: document.documentElement.style.colorScheme,
  svelteGlobals: Object.keys(window).filter((key) => key.startsWith("__sveltekit")),
  nodes: document.querySelectorAll("body *").length,
  text: (document.body.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 100),
  csp: document.querySelector('meta[http-equiv="content-security-policy"]')?.content ?? null,
}))()`;

/**
 * Load a url in a window and collect what the page did.
 *
 * @param {BrowserWindow} win Window to load into
 * @param {string} url Url to load
 * @returns {Promise<{violations: string[], early: any, late: any}>} Observations
 */
function observe(win, url) {
  return new Promise((resolve, reject) => {
    const violations = [];
    let early = null;
    let settled = false;

    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };

    const timer = setTimeout(
      () => finish(new Error(`the page did not finish loading within ${PHASE_TIMEOUT_MS} ms`)),
      PHASE_TIMEOUT_MS,
    );

    win.webContents.on("console-message", (_event, _level, message) => {
      if (/Content Security Policy|Refused to (execute|load|apply|run)/i.test(message)) {
        violations.push(message);
      }
    });

    win.webContents.on("did-fail-load", (_event, code, description, failedUrl) => {
      if (failedUrl === url) {
        finish(new Error(`${failedUrl} failed to load: ${description} (${code})`));
      }
    });

    win.webContents.on("render-process-gone", (_event, details) => {
      finish(new Error(`the renderer process is gone: ${details.reason}`));
    });

    win.webContents.on("dom-ready", () => {
      // The head scripts have run by now and the bundle has not mounted yet:
      // anything themed here came from the template script.
      win.webContents
        .executeJavaScript(PROBE, true)
        .then((state) => {
          early = state;
        })
        .catch(() => {});
    });

    win.webContents.once("did-finish-load", () => {
      setTimeout(() => {
        win.webContents
          .executeJavaScript(PROBE, true)
          .then((late) => finish(null, { violations, early, late }))
          .catch((error) => finish(error));
      }, MOUNT_DELAY_MS);
    });

    win.loadURL(url).catch((error) => finish(error));
  });
}

/**
 * Judge one phase against its expectation.
 *
 * @param {object} phase Phase descriptor
 * @param {{violations: string[], early: any, late: any}} observed Result of observe()
 * @returns {string[]} Problems, empty when the phase behaved as expected
 */
function judge(phase, observed) {
  const problems = [];
  const booted = observed.late.svelteGlobals.length > 0;
  const mounted = observed.late.nodes > 5;

  if (phase.expect.violations === 0) {
    if (observed.violations.length > 0) {
      problems.push(`the engine reported ${observed.violations.length} policy violation(s):`);
      problems.push(...observed.violations.map((message) => `  ${message}`));
    }
  } else if (observed.violations.length === 0) {
    problems.push(
      "the engine reported no violation although the page carries a script without a hash: " +
        "the policy is not enforced, every check built on it is vacuous",
    );
  }

  if (booted !== phase.expect.booted) {
    problems.push(
      phase.expect.booted
        ? "the sveltekit bootstrap script did not run, so the app can not start"
        : "the sveltekit bootstrap script ran although its hash was removed",
    );
  }
  if (mounted !== phase.expect.mounted) {
    problems.push(
      phase.expect.mounted
        ? `the SPA did not mount (${observed.late.nodes} body nodes, ${JSON.stringify(observed.late.text)})`
        : `the SPA mounted although its bootstrap script should have been blocked (${observed.late.nodes} body nodes)`,
    );
  }

  if (phase.expect.prePaint === "dark") {
    const dark = /(^|\s)dark(\s|$)/.test(observed.early?.htmlClass ?? "");
    if (!dark || observed.early?.colorScheme !== "dark") {
      problems.push(
        "the pre-paint theme script did not run before the first layout " +
          `(html class ${JSON.stringify(observed.early?.htmlClass ?? null)}, ` +
          `color-scheme ${JSON.stringify(observed.early?.colorScheme ?? null)})`,
      );
    }
  }

  return problems;
}

/**
 * Build the target under test.
 *
 * @param {string} name renderer | frontend
 * @returns {object} Target descriptor
 */
function buildTarget(name) {
  const frontend = name === "frontend";
  const templatePath = frontend
    ? path.join(repoRoot, "frontend", "src", "app.html")
    : path.join(webappRoot, "renderer", "app.html");
  const buildDir = frontend ? path.join(repoRoot, "frontend", "build") : path.join(webappRoot, "dist", "renderer");

  if (!fs.existsSync(path.join(buildDir, "index.html"))) {
    throw new Error(`${path.relative(repoRoot, buildDir)}/index.html not found, run the build first`);
  }

  const template = fs.readFileSync(templatePath, "utf8");
  const realHtml = fs.readFileSync(path.join(buildDir, "index.html"), "utf8");
  const templateHashes = [...new Set(csp.inlineScripts(template).flatMap((body) => csp.scriptHashes(body)))];
  const pageHashes = [...new Set(csp.inlineScripts(realHtml).flatMap((body) => csp.scriptHashes(body)))];
  const bootstrapHashes = pageHashes.filter((hash) => !templateHashes.includes(hash));

  if (templateHashes.length === 0) {
    throw new Error("the built page carries no hash of the template script, there is nothing to strip");
  }
  if (bootstrapHashes.length === 0) {
    throw new Error("the built page carries no hash beyond the template ones, the bootstrap hash is missing");
  }

  // The phases below derive their variants from an intact page: without this
  // precondition a broken build would be reported through the phases, with
  // conclusions about scripts that were never testable.
  const broken = csp.verifyPage(realHtml, template);
  if (broken.length > 0) {
    throw new Error(
      `the build itself does not satisfy the policy, run the build again:\n${broken.map((problem) => `  ${problem}`).join("\n")}`,
    );
  }

  const phases = [
    {
      id: "real-build",
      label: "the real build loads and boots",
      html: realHtml,
      expect: { violations: 0, booted: true, mounted: true, prePaint: "dark" },
    },
    {
      id: "template-hash-removed",
      label: "the template script is blocked when its hash is gone",
      html: stripHashes(realHtml, templateHashes),
      expect: { violations: 1, booted: true, mounted: true },
    },
    {
      id: "bootstrap-hash-removed",
      label: "the app does not boot when the bootstrap hash is gone",
      html: stripHashes(realHtml, bootstrapHashes),
      expect: { violations: 1, booted: false, mounted: false },
    },
  ];

  return frontend ? frontendTarget(buildDir, phases) : rendererTarget(buildDir, phases);
}

/**
 * The electron app bundle: served over app:// by the production protocol.
 *
 * @param {string} buildDir Built renderer directory
 * @param {object[]} phases Phases to run
 * @returns {object} Target descriptor
 */
function rendererTarget(buildDir, phases) {
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "alasio-csp-renderer-"));
  const serveDir = path.join(workDir, "renderer");
  fs.cpSync(buildDir, serveDir, { recursive: true });

  // The renderer reads the host state synchronously on startup; the theme it
  // reports decides the branch the pre-paint script takes.
  const sharedState = {
    backendPort: 0,
    route: "loading",
    language: "en-US",
    configLang: "system",
    theme: "dark",
    configTheme: "dark",
    dpiScaling: true,
    isFirstTimeSetup: false,
    backendStatus: "failed",
  };

  const ipc = loadTypeScriptModule(path.join(webappRoot, "shared", "ipc.ts"));
  const { registerAppProtocol } = loadTypeScriptModule(path.join(webappRoot, "main", "protocol.ts"));
  registerAppProtocol(serveDir);

  return {
    name: "renderer (app://bundle/)",
    phases,
    prepare() {
      const preload = path.join(webappRoot, "dist", "preload", "index.js");
      ipcMain.on(ipc.IPC_SHARED_STATE_GET_SYNC, (event) => {
        event.returnValue = sharedState;
      });
      ipcMain.handle(ipc.IPC_SHARED_STATE_GET, () => sharedState);
      ipcMain.handle(ipc.IPC_WINDOW_IS_MAXIMIZED, () => false);
      ipcMain.handle(ipc.IPC_WINDOW_CONFIRM_CLOSE, () => undefined);
      ipcMain.handle(ipc.IPC_BACKEND_LOG_SUBSCRIBE, () => []);
      ipcMain.handle(ipc.IPC_BACKEND_START, () => undefined);
      return { preload: fs.existsSync(preload) ? preload : undefined };
    },
    preparePhase(phase) {
      fs.writeFileSync(path.join(serveDir, "index.html"), phase.html);
    },
    url: (phase) => `app://bundle/?phase=${phase.id}`,
    cleanup() {
      fs.rmSync(workDir, { recursive: true, force: true });
    },
  };
}

/**
 * The web ui served by the python backend: http, with the meta mirrored into
 * the response header exactly like alasio/backend/app/frontend.py does.
 *
 * The page carries the policy twice (meta and header) and the browser enforces
 * their intersection, so a blocked script is reported once per policy: the
 * violation counts of this target are twice the ones of the renderer, which is
 * the production behaviour and not a duplicate.
 *
 * @param {string} buildDir Built frontend directory
 * @param {object[]} phases Phases to run
 * @returns {object} Target descriptor
 */
function frontendTarget(buildDir, phases) {
  const byPhase = new Map(phases.map((phase) => [phase.id, phase.html]));
  let server = null;
  let origin = "";

  return {
    name: "frontend (http)",
    phases,
    async prepare() {
      server = http.createServer((request, response) => {
        const requestUrl = new URL(request.url, "http://127.0.0.1");
        const pathname = decodeURIComponent(requestUrl.pathname);
        if (pathname === "/" || pathname === "/index.html") {
          const html =
            byPhase.get(requestUrl.searchParams.get("phase")) ??
            fs.readFileSync(path.join(buildDir, "index.html"), "utf8");
          response.writeHead(200, {
            "Content-Type": MIME[".html"],
            "Cache-Control": "no-store",
            "Content-Security-Policy": headerPolicy(html),
          });
          response.end(html);
          return;
        }
        const filePath = path.join(buildDir, pathname);
        if (!filePath.startsWith(buildDir + path.sep)) {
          response.writeHead(403).end();
          return;
        }
        let body;
        try {
          body = fs.readFileSync(filePath);
        } catch {
          response.writeHead(404).end();
          return;
        }
        response.writeHead(200, {
          "Content-Type": MIME[path.extname(filePath)] ?? "application/octet-stream",
          "Cache-Control": "no-store",
        });
        response.end(body);
      });
      await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
      origin = `http://127.0.0.1:${server.address().port}`;
      return {};
    },
    preparePhase() {},
    // Remote sessions without ?embedded=electron: the supported browser mode.
    url: (phase) => `${origin}/?theme=dark&themeConfig=dark&phase=${phase.id}`,
    cleanup() {
      if (server) server.close();
    },
  };
}

/**
 * Run every phase and report.
 */
async function main() {
  const options = parseArgs(process.argv.slice(2));
  const name = options.target ?? "renderer";

  // An unknown target must not fall back to the default: a typo in the ci
  // would then silently run the renderer smoke and never test the page it
  // was supposed to test.
  if (name !== "renderer" && name !== "frontend") {
    console.error(`[smoke] unknown --target ${JSON.stringify(name)}, expected renderer or frontend`);
    app.exit(2);
    return;
  }

  let target;
  try {
    target = buildTarget(name);
  } catch (error) {
    console.error(`[smoke] ${error instanceof Error ? error.message : error}`);
    app.exit(2);
    return;
  }

  console.log(`[smoke] target: ${target.name}`);

  // registerAppProtocol() was already called at module scope for the renderer
  // target (the scheme privileges have to be registered before app ready).
  await app.whenReady();
  const windowOptions = await target.prepare();

  const failures = [];
  try {
    for (const [index, phase] of target.phases.entries()) {
      target.preparePhase(phase);
      const win = new BrowserWindow({
        show: false,
        width: 960,
        height: 660,
        webPreferences: {
          preload: windowOptions.preload,
          contextIsolation: true,
          nodeIntegration: false,
        },
      });
      let observed;
      try {
        observed = await observe(win, target.url(phase));
      } catch (error) {
        failures.push(`phase ${index + 1}/3 ${phase.label}: ${error instanceof Error ? error.message : error}`);
        win.destroy();
        continue;
      }
      win.destroy();

      const problems = judge(phase, observed);
      const summary =
        `${observed.violations.length} violation(s), ` +
        `${observed.late.svelteGlobals.length > 0 ? "booted" : "not booted"}, ` +
        `${observed.late.nodes} body node(s)`;
      if (problems.length === 0) {
        console.log(`[smoke] phase ${index + 1}/3 ${phase.label}: PASSED (${summary})`);
      } else {
        console.log(`[smoke] phase ${index + 1}/3 ${phase.label}: FAILED (${summary})`);
        // The log alone has to be enough to diagnose a CI failure: the whole
        // observation is printed, the document itself is not needed.
        console.log(`[smoke]   observed: ${JSON.stringify(observed, null, 2)}`);
        failures.push(...problems.map((problem) => `phase ${index + 1}/3: ${problem}`));
      }
    }
  } finally {
    target.cleanup();
  }

  if (failures.length > 0) {
    console.error("[smoke] FAILED");
    for (const failure of failures) {
      console.error(`[smoke]   ${failure}`);
    }
    app.exit(1);
    return;
  }

  console.log("[smoke] OK: the policy is enforced and the built page satisfies it");
  app.exit(0);
}

// Match the app: no GPU, no sandbox (the renderer does not need either).
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-http-cache");

// A window is created and destroyed per phase. Without this listener
// electron's default behaviour quits the app the first time no window is left.
app.on("window-all-closed", () => {});

main().catch((error) => {
  console.error("[smoke] the smoke test itself failed:", error);
  app.exit(1);
});
