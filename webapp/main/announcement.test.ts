/**
 * Unit tests for the token announcement parsing and chained validation.
 */
import { describe, expect, it } from "vitest";
import { acceptAnnouncement, parseAnnouncement } from "./announcement";

const T1 = "a".repeat(64);
const T2 = "b".repeat(64);
const T3 = "c".repeat(64);

describe("TestParseAnnouncement", () => {
  it("accepts the initial begin announcement", () => {
    expect(parseAnnouncement(`[Supervisor] token_set:begin:${T1}`)).toEqual({ old: "begin", next: T1 });
  });

  it("accepts a rotation announcement", () => {
    expect(parseAnnouncement(`[Supervisor] token_set:${T1}:${T2}`)).toEqual({ old: T1, next: T2 });
  });

  it("rejects malformed lines", () => {
    expect(parseAnnouncement("")).toBeNull();
    expect(parseAnnouncement("[Supervisor] token_set:")).toBeNull();
    expect(parseAnnouncement("[Supervisor] token_set:begin")).toBeNull();
    expect(parseAnnouncement("[Supervisor] token_set:begin:")).toBeNull();
    // wrong prefix
    expect(parseAnnouncement(`[Backend] token_set:begin:${T1}`)).toBeNull();
    // non-hex token
    expect(parseAnnouncement(`[Supervisor] token_set:begin:xyz${T1.slice(3)}`)).toBeNull();
    // trailing content
    expect(parseAnnouncement(`[Supervisor] token_set:begin:${T1} extra`)).toBeNull();
    // uppercase hex rejected (regex is lowercase only)
    expect(parseAnnouncement(`[Supervisor] token_set:begin:${T1.toUpperCase()}`)).toBeNull();
  });
});

describe("TestAcceptAnnouncement", () => {
  it("accepts begin when current is null", () => {
    expect(acceptAnnouncement(null, "begin", T1)).toBe(T1);
  });

  it("ignores a forged begin after the chain started", () => {
    // once authToken is non-null, begin:<fake> can never match
    expect(acceptAnnouncement(T1, "begin", T2)).toBeNull();
  });

  it("accepts a rotation when old matches current", () => {
    expect(acceptAnnouncement(T1, T1, T2)).toBe(T2);
  });

  it("ignores a rotation when old mismatches", () => {
    // forged / out-of-chain announcement: old != current
    expect(acceptAnnouncement(T1, T2, T3)).toBeNull();
    expect(acceptAnnouncement(T1, "begin", T3)).toBeNull();
  });

  it("rejects a forged announcement that cannot collide with the chain", () => {
    // the attacker does not know the current token; a begin sentinel after
    // the chain started and a random old token are both rejected
    let current: string | null = null;
    const step = (line: string) => {
      const parsed = parseAnnouncement(line);
      if (!parsed) return null;
      const next = acceptAnnouncement(current, parsed.old, parsed.next);
      if (next) current = next;
      return next;
    };

    // legitimate chain
    expect(step(`[Supervisor] token_set:begin:${T1}`)).toBe(T1);
    expect(step(`[Supervisor] token_set:${T1}:${T2}`)).toBe(T2);
    // forged attempts after the chain started
    expect(step(`[Supervisor] token_set:begin:${T3}`)).toBeNull();
    expect(step(`[Supervisor] token_set:${T1}:${T3}`)).toBeNull();
    // current unchanged
    expect(current).toBe(T2);
  });
});
