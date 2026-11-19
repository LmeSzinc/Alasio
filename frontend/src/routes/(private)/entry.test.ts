/**
 * Tests for the entry configuration selection (entry.ts)
 *
 * The app opens the first configuration of the sidebar when the user enters
 * without one. A fresh install has none until the backend finished creating its
 * default configuration, so the selection must tell "no data yet" apart from "a
 * configuration to open".
 */
import { describe, expect, it } from "vitest";
import type { ConfigLike, ConfigTopicLike } from "$lib/components/aside/types";
import { configOverviewPath, firstConfig } from "./entry";

/** A configuration as the ConfigScan topic carries it. */
function config(name: string, gid: number, iid: number): ConfigLike {
  return { id: 0, name, mod: "TestMod", gid, iid };
}

describe("firstConfig", () => {
  it("has no answer while the topic has no data", () => {
    expect(firstConfig(undefined)).toBeUndefined();
  });

  it("has no answer for an empty topic", () => {
    expect(firstConfig({})).toBeUndefined();
  });

  it("returns the only configuration of the topic", () => {
    const only = config("alasio", 1, 1);

    expect(firstConfig({ alasio: only })).toBe(only);
  });

  it("returns the first configuration of the sidebar order, not of the insertion order", () => {
    const topic: ConfigTopicLike = {
      z: config("z", 3, 1),
      b: config("b", 1, 2),
      a: config("a", 1, 1),
    };

    expect(firstConfig(topic)?.name).toBe("a");
  });

  it("prefers the lower scheduler group over a lower slot", () => {
    const topic: ConfigTopicLike = { b: config("b", 2, 1), a: config("a", 1, 9) };

    expect(firstConfig(topic)?.name).toBe("a");
  });

  it("prefers the lower slot inside the same scheduler group", () => {
    const topic: ConfigTopicLike = { b: config("b", 1, 2), a: config("a", 1, 1) };

    expect(firstConfig(topic)?.name).toBe("a");
  });

  it("keeps the first configuration when two share a position", () => {
    const topic: ConfigTopicLike = { b: config("b", 1, 1), a: config("a", 1, 1) };

    expect(firstConfig(topic)?.name).toBe("b");
  });

  it("leaves the order of the topic data untouched", () => {
    // the record is the live topic object, shared by every reader of ConfigScan
    const topic: ConfigTopicLike = { b: config("b", 2, 1), a: config("a", 1, 2) };

    firstConfig(topic);

    expect(Object.keys(topic)).toEqual(["b", "a"]);
  });
});

describe("configOverviewPath", () => {
  it.each([
    ["alasio", "/config/alasio/overview"],
    ["my config", "/config/my%20config/overview"],
    ["a/b", "/config/a%2Fb/overview"],
    ["配置", "/config/%E9%85%8D%E7%BD%AE/overview"],
  ])("builds the overview route of %s", (name, expected) => {
    expect(configOverviewPath(name)).toBe(expected);
  });
});
