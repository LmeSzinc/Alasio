/**
 * Unit tests for the main process startup argument parser.
 *
 * Run with: npx tsx --test webapp/main/startup-args.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseStartupWindowMode } from "./startup-args";

/** Packaged client: the executable is argv[0], every switch follows it. */
const packaged = (...args: string[]) => ["Alasio.exe", ...args];

/** Development: `electron .` puts the app directory between both. */
const dev = (...args: string[]) => ["electron.exe", ".", ...args];

test("parseStartupWindowMode: no switch means the default startup", () => {
  assert.equal(parseStartupWindowMode(packaged()), "default");
  assert.equal(parseStartupWindowMode(dev()), "default");
});

test("parseStartupWindowMode: reads the three window modes", () => {
  assert.equal(parseStartupWindowMode(packaged("--window=minimize")), "minimize");
  assert.equal(parseStartupWindowMode(packaged("--window=maximize")), "maximize");
  assert.equal(parseStartupWindowMode(packaged("--window=tray")), "tray");
});

test("parseStartupWindowMode: takes the value after a bare switch too", () => {
  assert.equal(parseStartupWindowMode(packaged("--window", "tray")), "tray");
  assert.equal(parseStartupWindowMode(dev("--window", "maximize")), "maximize");
});

test("parseStartupWindowMode: an invalid value means the default startup", () => {
  assert.equal(parseStartupWindowMode(packaged("--window=bogus")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window=")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window")), "default");
  // values are matched exactly, close spellings do not count
  assert.equal(parseStartupWindowMode(packaged("--window=Minimize")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window=minimized")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window=tray ")), "default");
});

test("parseStartupWindowMode: ignores switches that share the prefix", () => {
  // chrome's --window-size/--window-position and the web platform names
  // must never be mistaken for --window
  assert.equal(parseStartupWindowMode(packaged("--window-size=800,600")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window-position=0,0")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window-controls-overlay")), "default");
  assert.equal(parseStartupWindowMode(packaged("--new-window")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window-all-closed")), "default");
});

test("parseStartupWindowMode: the last occurrence wins", () => {
  assert.equal(parseStartupWindowMode(packaged("--window=tray", "--window=maximize")), "maximize");
  assert.equal(parseStartupWindowMode(packaged("--window=maximize", "--window", "tray")), "tray");
  // a trailing invalid value falls back like any other invalid input
  assert.equal(parseStartupWindowMode(packaged("--window=tray", "--window=bogus")), "default");
});

test("parseStartupWindowMode: a bare switch does not take another switch as value", () => {
  assert.equal(parseStartupWindowMode(packaged("--window", "--no-sandbox")), "default");
  assert.equal(parseStartupWindowMode(packaged("--window", "--window=minimize")), "minimize");
});

test("parseStartupWindowMode: other client switches are left alone", () => {
  const argv = packaged("--no-sandbox", "--remote-debugging-port=9222", "--window=tray", "--disable-gpu");
  assert.equal(parseStartupWindowMode(argv), "tray");
});
