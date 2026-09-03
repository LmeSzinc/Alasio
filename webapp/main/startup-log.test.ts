/**
 * Unit tests for StartupLogBuffer.
 *
 * Run with: npx tsx --test webapp/main/startup-log.test.ts
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { StartupLogBuffer } from "./startup-log";

test("StartupLogBuffer: starts empty, unsubscribed", () => {
  const log = new StartupLogBuffer();
  assert.equal(log.isSubscribed, false);
  assert.deepEqual(log.subscribe(), []);
});

test("StartupLogBuffer: snapshot is chronological (oldest first)", () => {
  const log = new StartupLogBuffer();
  log.record("a");
  log.record("b");
  log.record("c");
  assert.deepEqual(log.subscribe(), ["a", "b", "c"]);
});

test("StartupLogBuffer: record drops the oldest line over the cap", () => {
  const log = new StartupLogBuffer();
  for (let i = 1; i <= 501; i++) {
    log.record(`line ${i}`);
  }
  const snapshot = log.subscribe();
  assert.equal(snapshot.length, 500);
  assert.equal(snapshot[0], "line 2");
  assert.equal(snapshot[499], "line 501");
});

test("StartupLogBuffer: subscribe is the one-way transition into push mode", () => {
  const log = new StartupLogBuffer();
  log.record("before");
  log.subscribe();
  assert.equal(log.isSubscribed, true);
  // Records after the subscription stay buffered too (future replays).
  log.record("after");
  assert.deepEqual(log.subscribe(), ["before", "after"]);
});

test("StartupLogBuffer: subscribe is idempotent and replays the full buffer", () => {
  const log = new StartupLogBuffer();
  log.record("a");
  const first = log.subscribe();
  log.record("b");
  // Second subscription (renderer reload) replays everything so far.
  const second = log.subscribe();
  assert.deepEqual(first, ["a"]);
  assert.deepEqual(second, ["a", "b"]);
});

test("StartupLogBuffer: snapshot is a copy, not a live view", () => {
  const log = new StartupLogBuffer();
  log.record("a");
  const snapshot = log.subscribe();
  snapshot.push("mutated");
  snapshot.shift();
  assert.deepEqual(log.subscribe(), ["a"]);
});

test("StartupLogBuffer: clear empties the buffer", () => {
  const log = new StartupLogBuffer();
  log.record("a");
  log.record("b");
  log.clear();
  assert.deepEqual(log.subscribe(), []);
  log.record("c");
  assert.deepEqual(log.subscribe(), ["c"]);
});

test("StartupLogBuffer: clear keeps the subscribed flag", () => {
  const log = new StartupLogBuffer();
  log.record("old attempt");
  log.subscribe();
  log.clear();
  // Retry: a new attempt starts from an empty buffer, the subscribed
  // renderer keeps receiving new lines without re-subscribing.
  assert.equal(log.isSubscribed, true);
  log.record("new attempt");
  assert.deepEqual(log.subscribe(), ["new attempt"]);
});
