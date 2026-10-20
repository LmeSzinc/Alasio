/**
 * Unit tests for the main process startup argument parser.
 */
import { describe, expect, it } from "vitest";
import { parseStartupWindowMode } from "./startup-args";

/** Packaged client: the executable is argv[0], every switch follows it. */
const packaged = (...args: string[]) => ["Alasio.exe", ...args];

/** Development: `electron .` puts the app directory between both. */
const dev = (...args: string[]) => ["electron.exe", ".", ...args];

describe("TestParseStartupWindowMode", () => {
  it("treats no switch as the default startup", () => {
    expect(parseStartupWindowMode(packaged())).toBe("default");
    expect(parseStartupWindowMode(dev())).toBe("default");
  });

  it("reads the three window modes", () => {
    expect(parseStartupWindowMode(packaged("--window=minimize"))).toBe("minimize");
    expect(parseStartupWindowMode(packaged("--window=maximize"))).toBe("maximize");
    expect(parseStartupWindowMode(packaged("--window=tray"))).toBe("tray");
  });

  it("takes the value after a bare switch too", () => {
    expect(parseStartupWindowMode(packaged("--window", "tray"))).toBe("tray");
    expect(parseStartupWindowMode(dev("--window", "maximize"))).toBe("maximize");
  });

  it("falls back to the default startup on an invalid value", () => {
    expect(parseStartupWindowMode(packaged("--window=bogus"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window="))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window"))).toBe("default");
    // values are matched exactly, close spellings do not count
    expect(parseStartupWindowMode(packaged("--window=Minimize"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window=minimized"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window=tray "))).toBe("default");
  });

  it("ignores switches that share the prefix", () => {
    // chrome's --window-size/--window-position and the web platform names
    // must never be mistaken for --window
    expect(parseStartupWindowMode(packaged("--window-size=800,600"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window-position=0,0"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window-controls-overlay"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--new-window"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window-all-closed"))).toBe("default");
  });

  it("takes the last occurrence", () => {
    expect(parseStartupWindowMode(packaged("--window=tray", "--window=maximize"))).toBe("maximize");
    expect(parseStartupWindowMode(packaged("--window=maximize", "--window", "tray"))).toBe("tray");
    // a trailing invalid value falls back like any other invalid input
    expect(parseStartupWindowMode(packaged("--window=tray", "--window=bogus"))).toBe("default");
  });

  it("does not take another switch as the value of a bare switch", () => {
    expect(parseStartupWindowMode(packaged("--window", "--no-sandbox"))).toBe("default");
    expect(parseStartupWindowMode(packaged("--window", "--window=minimize"))).toBe("minimize");
  });

  it("leaves other client switches alone", () => {
    const argv = packaged("--no-sandbox", "--remote-debugging-port=9222", "--window=tray", "--disable-gpu");
    expect(parseStartupWindowMode(argv)).toBe("tray");
  });
});
