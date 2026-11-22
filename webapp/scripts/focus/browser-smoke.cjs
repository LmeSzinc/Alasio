"use strict";
// Browser smoke test for the window controls focus guard, in the real engine.
//
// LOCAL CHECK ONLY — this is not part of CI, and it can not be. The defect it
// covers only exists in a GUI session: closing DevTools gives the page its
// focus back, and with nothing focused in the page Blink runs its *initial
// focus* and focuses the first sequentially focusable element. Measured on
// windows with electron 22.3.27, the behaviour needs
//
//   - a visible window that really owns the foreground (`show: true`), and
//   - the DevTools view actually taking the focus away from the page while it
//     is open.
//
// A hidden window reproduces nothing: with `show: false` the web contents never
// reports focus (not even after `webContents.focus()`), so no initial focus
// runs when DevTools closes — docked and detached alike. That is the mode the
// CI smoke job runs in (scripts/csp/browser-smoke.cjs creates `show: false`
// windows), which would make a CI gate vacuous rather than strict. The same
// check on a desktop is also racy: DevTools sometimes leaves the focus on the
// page, and such an attempt simply does not exercise the path.
//
// So the script reports three outcomes instead of two:
//
//   OK       every phase behaved as expected;
//   FAILED   a phase contradicts the expectation;
//   SKIPPED  the environment can not put the window into the state the check
//            needs (no interactive desktop, or DevTools never took the focus).
//            Exit code 0 on purpose: an environment that can not run the check
//            must not look like a regression. The marker says so loudly.
//
// The phases are ordered so the positive results mean something:
//
//   1. without the guard, closing DevTools leaves the focus (and its ring) on
//      the first window control — the reported bug. If this stops happening,
//      the engine changed and the guard is no longer justified;
//   2. with the guard, the same cycle ends with nothing focused;
//   3. with the guard, Tab still focuses the first control and draws its ring;
//   4. with the guard, clicking a control keeps the focus on it.
//
// The page is a fixture, not the built renderer: the build is not part of this
// check and would tie it to a prior `vite build`. The fixture mirrors what the
// guard depends on — the window controls group with the same DOM order and the
// shadcn button focus ring, spelled in rgb because electron 22 is Chromium 108
// and has no oklch.
//
// Run with:
//   pnpm run smoke:focus        (webapp, on a desktop)
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const ts = require("typescript");
const { app, BrowserWindow } = require("electron");

const webappRoot = path.resolve(__dirname, "..", "..");
const GUARD_FILE = path.join(webappRoot, "renderer", "lib", "windowControlsFocus.ts");

// DevTools takes the focus when it opens and gives it back when it closes; the
// focus transition happens on the engine's own schedule, not on a promise.
const DEVTOOLS_SETTLE_MS = 1200;
// How many times a phase retries before the environment is declared unable to
// run the check. DevTools does not always take the focus, and only the attempts
// where it did prove anything.
const MAX_ATTEMPTS = 4;

/**
 * @typedef {{ active: string | null, focusVisible: boolean, ring: string }} Snapshot
 * @typedef {{ active: string | null, focusVisible: boolean, ring: boolean }} Expectation
 * @typedef {{
 *   id: string,
 *   label: string,
 *   guard: boolean,
 *   after?: "tab" | "click",
 *   clickTarget?: string,
 *   expect: Expectation,
 * }} Phase
 * @typedef {{ snapshot: Snapshot, exercised: boolean }} Cycle
 */

/**
 * Wait for a while.
 *
 * @param {number} ms Milliseconds to wait
 * @returns {Promise<void>} Resolves after the delay
 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wait until `predicate` holds.
 *
 * The engine reports neither the window activation nor the focus through an
 * event this script can await, so every phase waits for the real state instead
 * of assuming a delay.
 *
 * @param {() => boolean} predicate Condition to wait for
 * @param {number} [timeoutMs] How long to wait, 3000 ms by default
 * @returns {Promise<boolean>} Whether the condition held before the timeout
 */
async function waitFor(predicate, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(50);
  }
  return predicate();
}

/**
 * Compile the guard module to CommonJS source for injection into the page.
 *
 * The module imports nothing, so a plain transpile is enough.
 *
 * @returns {string} JavaScript source of the module
 */
function compileGuard() {
  const source = fs.readFileSync(GUARD_FILE, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
    fileName: GUARD_FILE,
  });
  return outputText;
}

/**
 * The page under test.
 *
 * The ring mirrors the shadcn `Button` base class in the renderer
 * (`focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50`)
 * with the light theme's `--ring` value.
 *
 * @returns {string} Html document
 */
function fixture() {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>window controls focus fixture</title>
    <style>
      :root { --ring: rgb(142, 81, 255); }
      body { margin: 0; font-family: sans-serif; }
      #controls { display: flex; justify-content: flex-end; gap: 4px; padding: 6px; }
      .btn {
        width: 36px; height: 36px; padding: 0; cursor: pointer;
        border: 1px solid transparent; border-radius: 8px;
        background: transparent; background-clip: padding-box; outline: none;
      }
      .btn:focus-visible {
        border-color: var(--ring);
        box-shadow: 0 0 0 3px rgb(142 81 255 / 50%);
      }
    </style>
  </head>
  <body>
    <div id="controls">
      <button class="btn" id="hide" title="Hide to tray">H</button>
      <button class="btn" id="min" title="Minimize">M</button>
      <button class="btn" id="max" title="Maximize">X</button>
      <button class="btn" id="close" title="Close">C</button>
    </div>
    <p>A page body that never takes the focus by itself.</p>
  </body>
</html>`;
}

/**
 * Reads the focus and the ring of the focused element.
 *
 * @returns {Snapshot} State of the page
 */
const PROBE = `(() => {
  const el = document.activeElement;
  const style = el ? getComputedStyle(el) : null;
  return {
    active: el ? (el.id || el.tagName) : null,
    focusVisible: el && el.matches ? el.matches(":focus-visible") : false,
    ring: style ? (style.boxShadow === "none" ? "none" : style.boxShadow) : "none",
  };
})()`;

/**
 * Read the page state.
 *
 * @param {BrowserWindow} win Window under test
 * @returns {Promise<Snapshot>} State of the page
 */
function probe(win) {
  return win.webContents.executeJavaScript(PROBE, true);
}

/**
 * Install the guard on the controls group.
 *
 * @param {BrowserWindow} win Window holding the fixture
 * @returns {Promise<void>} Resolves once the guard is listening
 */
async function installGuard(win) {
  await win.webContents.executeJavaScript(
    `(() => {
      const module = { exports: {} };
      new Function("exports", "module", ${JSON.stringify(compileGuard())})(module.exports, module);
      module.exports.guardWindowControlsFocus(document.getElementById("controls"));
      return true;
    })()`,
    true,
  );
}

/**
 * Open and close DevTools once.
 *
 * `exercised` reports whether the attempt put the page through the state the
 * guard exists for: DevTools holding the focus, the page not. When DevTools
 * leaves the focus on the page, closing it runs no initial focus and the
 * attempt proves nothing either way.
 *
 * @param {BrowserWindow} win Window under test
 * @returns {Promise<Cycle>} Snapshot after closing, and whether the path ran
 */
async function cycleDevTools(win) {
  win.webContents.openDevTools({ mode: "right" });
  await sleep(DEVTOOLS_SETTLE_MS);
  const exercised = !win.webContents.isFocused();
  win.webContents.closeDevTools();
  await sleep(DEVTOOLS_SETTLE_MS);
  return { snapshot: await probe(win), exercised };
}

/**
 * Press a key through the engine's input pipeline.
 *
 * @param {BrowserWindow} win Window under test
 * @param {string} keyCode Electron key code
 * @returns {Promise<void>} Resolves after the key was handled
 */
async function pressKey(win, keyCode) {
  win.webContents.sendInputEvent({ type: "keyDown", keyCode });
  win.webContents.sendInputEvent({ type: "keyUp", keyCode });
  await sleep(300);
}

/**
 * Click the center of a control through the engine's input pipeline.
 *
 * @param {BrowserWindow} win Window under test
 * @param {string} id Element id
 * @returns {Promise<void>} Resolves after the click was handled
 */
async function clickControl(win, id) {
  const point = await win.webContents.executeJavaScript(
    `(() => {
      const rect = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect();
      return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
    })()`,
    true,
  );
  win.webContents.sendInputEvent({ type: "mouseDown", x: point.x, y: point.y, button: "left", clickCount: 1 });
  win.webContents.sendInputEvent({ type: "mouseUp", x: point.x, y: point.y, button: "left", clickCount: 1 });
  await sleep(300);
}

/**
 * Run one phase in a fresh window.
 *
 * Returns `null` when the environment never reached the state the phase needs
 * (the window did not get the focus, or DevTools never took it away from the
 * page), so the caller can report a skip instead of a failure.
 *
 * @param {Phase} phase Phase descriptor
 * @param {string} origin Fixture origin
 * @returns {Promise<Snapshot | null>} Observed state, or `null` when skipped
 */
async function runPhase(phase, origin) {
  const win = new BrowserWindow({
    show: true,
    width: 760,
    height: 360,
    frame: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  try {
    await win.loadURL(origin);
    win.show();
    win.focus();
    if (!(await waitFor(() => win.isFocused() && win.webContents.isFocused()))) {
      return null;
    }
    await sleep(300);

    if (phase.guard) await installGuard(win);

    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
      const cycle = await cycleDevTools(win);
      if (!cycle.exercised) continue;

      if (phase.after === "tab") {
        await pressKey(win, "Tab");
      } else if (phase.after === "click") {
        if (!phase.clickTarget) throw new Error("the click phase names no target");
        await clickControl(win, phase.clickTarget);
      }
      return phase.after ? await probe(win) : cycle.snapshot;
    }
    return null;
  } finally {
    win.destroy();
  }
}

/**
 * Judge one snapshot against the phase expectation.
 *
 * @param {Phase} phase Phase descriptor
 * @param {Snapshot} snapshot Observed focus state
 * @returns {string[]} Problems, empty when the phase behaved as expected
 */
function judge(phase, snapshot) {
  const problems = [];
  if (snapshot.active !== phase.expect.active) {
    problems.push(`expected the focus on ${String(phase.expect.active)}, found ${JSON.stringify(snapshot.active)}`);
  }
  if (snapshot.focusVisible !== phase.expect.focusVisible) {
    problems.push(
      `expected :focus-visible ${String(phase.expect.focusVisible)}, found ${JSON.stringify(snapshot.focusVisible)}`,
    );
  }
  if ((snapshot.ring !== "none") !== phase.expect.ring) {
    problems.push(
      `expected the focus ring ${phase.expect.ring ? "drawn" : "absent"}, found ${JSON.stringify(snapshot.ring)}`,
    );
  }
  return problems;
}

/** @type {Phase[]} */
const PHASES = [
  {
    id: "before-guard",
    label: "without the guard, closing DevTools focuses the first window control",
    // This is the bug being fixed; if it no longer reproduces, the guard is
    // solving a problem the engine stopped having.
    guard: false,
    expect: { active: "hide", focusVisible: true, ring: true },
  },
  {
    id: "guard",
    label: "with the guard, closing DevTools leaves nothing focused",
    guard: true,
    expect: { active: "BODY", focusVisible: false, ring: false },
  },
  {
    id: "guard-keyboard",
    label: "with the guard, Tab still focuses the first control and draws its ring",
    guard: true,
    after: "tab",
    expect: { active: "hide", focusVisible: true, ring: true },
  },
  {
    id: "guard-click",
    label: "with the guard, clicking a control keeps the focus on it",
    guard: true,
    after: "click",
    clickTarget: "hide",
    expect: { active: "hide", focusVisible: false, ring: false },
  },
];

/**
 * Serve the fixture on a loopback port.
 *
 * @returns {Promise<http.Server>} Listening server
 */
function serveFixture() {
  return new Promise((resolve) => {
    const server = http.createServer((_request, response) => {
      response.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(fixture());
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

/**
 * Read the listening port of a server.
 *
 * @param {http.Server} server Listening server
 * @returns {number} Bound port
 */
function boundPort(server) {
  const address = server.address();
  if (address && typeof address === "object") return address.port;
  throw new Error("the fixture server is not listening on a port");
}

async function main() {
  const server = await serveFixture();
  const origin = `http://127.0.0.1:${boundPort(server)}/`;

  const failures = [];
  const skipped = [];
  try {
    for (const [index, phase] of PHASES.entries()) {
      const position = `phase ${index + 1}/${PHASES.length}`;
      let snapshot;
      try {
        snapshot = await runPhase(phase, origin);
      } catch (error) {
        failures.push(`${position} ${phase.label}: ${error instanceof Error ? error.message : error}`);
        continue;
      }
      if (snapshot === null) {
        skipped.push(`${position} ${phase.label}: the environment can not focus the window`);
        continue;
      }
      const problems = judge(phase, snapshot);
      const summary = `active=${JSON.stringify(snapshot.active)} focus-visible=${String(snapshot.focusVisible)} ring=${JSON.stringify(snapshot.ring)}`;
      if (problems.length === 0) {
        console.log(`[smoke] ${position} ${phase.label}: PASSED (${summary})`);
      } else {
        console.log(`[smoke] ${position} ${phase.label}: FAILED (${summary})`);
        failures.push(...problems.map((problem) => `${position}: ${problem}`));
      }
    }
  } finally {
    server.close();
  }

  if (failures.length > 0) {
    console.error("[smoke] FAILED");
    for (const failure of failures) console.error(`[smoke]   ${failure}`);
    app.exit(1);
    return;
  }
  if (skipped.length > 0) {
    // Exit code 0 on purpose: an environment that can not run the check must
    // not look like a regression. The marker is what tells the two apart.
    for (const skip of skipped) console.log(`[smoke]   ${skip}`);
    console.log("[smoke] SKIPPED: no interactive desktop to reproduce the defect on");
    app.exit(0);
    return;
  }

  console.log("[smoke] OK: closing DevTools no longer focuses the window controls");
  app.exit(0);
}

// Match the app: no GPU, no sandbox (the fixture needs neither).
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");

// Windows are created and destroyed per phase; without this listener the
// default behaviour quits the app the first time no window is left.
app.on("window-all-closed", () => {});

app
  .whenReady()
  .then(main)
  .catch((error) => {
    console.error("[smoke] the smoke test itself failed:", error);
    app.exit(1);
  });
