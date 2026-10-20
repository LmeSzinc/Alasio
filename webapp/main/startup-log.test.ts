/**
 * Unit tests for StartupLogBuffer.
 */
import { describe, expect, it } from "vitest";
import { StartupLogBuffer } from "./startup-log";

describe("TestStartupLogBuffer", () => {
  it("starts empty, unsubscribed", () => {
    const log = new StartupLogBuffer();
    expect(log.isSubscribed).toBe(false);
    expect(log.subscribe()).toEqual([]);
  });

  it("keeps the snapshot chronological (oldest first)", () => {
    const log = new StartupLogBuffer();
    log.record("a");
    log.record("b");
    log.record("c");
    expect(log.subscribe()).toEqual(["a", "b", "c"]);
  });

  it("drops the oldest line over the cap", () => {
    const log = new StartupLogBuffer();
    for (let i = 1; i <= 501; i++) {
      log.record(`line ${i}`);
    }
    const snapshot = log.subscribe();
    expect(snapshot.length).toBe(500);
    expect(snapshot[0]).toBe("line 2");
    expect(snapshot[499]).toBe("line 501");
  });

  it("turns subscribe into the one-way transition to push mode", () => {
    const log = new StartupLogBuffer();
    log.record("before");
    log.subscribe();
    expect(log.isSubscribed).toBe(true);
    // Records after the subscription stay buffered too (future replays).
    log.record("after");
    expect(log.subscribe()).toEqual(["before", "after"]);
  });

  it("keeps subscribe idempotent and replays the full buffer", () => {
    const log = new StartupLogBuffer();
    log.record("a");
    const first = log.subscribe();
    log.record("b");
    // Second subscription (renderer reload) replays everything so far.
    const second = log.subscribe();
    expect(first).toEqual(["a"]);
    expect(second).toEqual(["a", "b"]);
  });

  it("returns a copy, not a live view", () => {
    const log = new StartupLogBuffer();
    log.record("a");
    const snapshot = log.subscribe();
    snapshot.push("mutated");
    snapshot.shift();
    expect(log.subscribe()).toEqual(["a"]);
  });

  it("empties the buffer on clear", () => {
    const log = new StartupLogBuffer();
    log.record("a");
    log.record("b");
    log.clear();
    expect(log.subscribe()).toEqual([]);
    log.record("c");
    expect(log.subscribe()).toEqual(["c"]);
  });

  it("keeps the subscribed flag on clear", () => {
    const log = new StartupLogBuffer();
    log.record("old attempt");
    log.subscribe();
    log.clear();
    // Retry: a new attempt starts from an empty buffer, the subscribed
    // renderer keeps receiving new lines without re-subscribing.
    expect(log.isSubscribed).toBe(true);
    log.record("new attempt");
    expect(log.subscribe()).toEqual(["new attempt"]);
  });
});
