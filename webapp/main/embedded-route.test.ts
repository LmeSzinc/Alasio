/**
 * Unit tests for the route the main process keeps for the embedded web app.
 *
 * The store is what makes a window reload reopen the page the user was on: it
 * has to accept a path of the frontend and nothing else, and the last valid
 * report has to win. The IPC wiring around it is electron behaviour; the
 * decisions are tested here.
 */
import { describe, expect, it, vi } from "vitest";

// electron is stubbed: the node environment cannot start it, and the points
// under test are the decisions of the store, not ipcMain's delivery.
vi.mock("electron", () => ({ ipcMain: { on: () => {} } }));

import { createEmbeddedRouteStore } from "./embedded-route";

describe("TestEmbeddedRouteStore", () => {
  it("keeps a reported route", () => {
    const store = createEmbeddedRouteStore();

    store.remember("/config/cfg_a/overview");

    expect(store.get()).toBe("/config/cfg_a/overview");
  });

  it("keeps query and hash of the route", () => {
    const store = createEmbeddedRouteStore();

    store.remember("/dev/setting?tab=display#top");

    expect(store.get()).toBe("/dev/setting?tab=display#top");
  });

  it("lets the last report win", () => {
    const store = createEmbeddedRouteStore();

    store.remember("/config/cfg_a/overview");
    store.remember("/config/cfg_b/arg");

    expect(store.get()).toBe("/config/cfg_b/arg");
  });

  it("drops a report that is not a path of the frontend", () => {
    const store = createEmbeddedRouteStore();
    store.remember("/config/cfg_a/overview");

    // A malformed report keeps the previous route instead of clearing it
    for (const invalid of ["", "//example.com/", "/\\example.com/", "app://bundle/", 42, null, undefined]) {
      store.remember(invalid);
      expect(store.get()).toBe("/config/cfg_a/overview");
    }
  });

  it("has nothing to reopen before the frontend reports", () => {
    expect(createEmbeddedRouteStore().get()).toBeNull();
  });

  it("starts empty in a new app run", () => {
    // The store is per app run on purpose: a closed client opens at the app
    // root again, so a fresh store must not inherit a route
    const first = createEmbeddedRouteStore();
    first.remember("/config/cfg_a/overview");

    expect(createEmbeddedRouteStore().get()).toBeNull();
  });
});
