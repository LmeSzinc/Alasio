/**
 * Unit tests for the relaunch request parsing.
 */
import { describe, expect, it } from "vitest";
import { parseRelaunch } from "./relaunch";

const TOKEN = "a".repeat(64);
const OTHER = "b".repeat(64);

describe("TestParseRelaunch", () => {
  it("accepts a request of the current chain", () => {
    expect(parseRelaunch(`[Supervisor] relaunch:${TOKEN}`, TOKEN)).toBe(true);
  });

  it("rejects a request carrying another token", () => {
    // a worker can write to the same stdout: the token is what makes the
    // request credible
    expect(parseRelaunch(`[Supervisor] relaunch:${OTHER}`, TOKEN)).toBe(false);
  });

  it("rejects every line before the first announcement", () => {
    expect(parseRelaunch(`[Supervisor] relaunch:${TOKEN}`, null)).toBe(false);
  });

  it("rejects malformed lines", () => {
    expect(parseRelaunch("", TOKEN)).toBe(false);
    expect(parseRelaunch("[Supervisor] relaunch:", TOKEN)).toBe(false);
    expect(parseRelaunch(`[Supervisor] relaunch:${TOKEN.slice(1)}`, TOKEN)).toBe(false);
    // uppercase hex is not the form the supervisor prints
    expect(parseRelaunch(`[Supervisor] relaunch:${TOKEN.toUpperCase()}`, TOKEN)).toBe(false);
    // trailing content
    expect(parseRelaunch(`[Supervisor] relaunch:${TOKEN} extra`, TOKEN)).toBe(false);
    // a token announcement is not a relaunch request
    expect(parseRelaunch(`[Supervisor] token_set:begin:${TOKEN}`, TOKEN)).toBe(false);
  });
});
