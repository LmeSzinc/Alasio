/**
 * Tests for the window controls width chain.
 *
 * The width decides how much of the header the frontend keeps clear on the
 * right. It must come from the host when the host speaks; a browser has no host
 * to follow, so `auto` reserves nothing there and only `always` simulates.
 */
import { describe, expect, it } from "vitest";
import { FALLBACK_CONTROLS_WIDTH, resolveControlsWidth } from "$lib/window/frame.svelte";

describe("TestResolveControlsWidth", () => {
  it("uses the host measurement first", () => {
    expect(resolveControlsWidth({ mode: "auto", host: 160, url: 168 })).toEqual({
      width: 160,
      source: "host",
    });
    expect(resolveControlsWidth({ mode: "always", host: 150, url: null })).toEqual({
      width: 150,
      source: "host",
    });
  });

  it("uses the iframe URL value before the first downlink", () => {
    expect(resolveControlsWidth({ mode: "auto", host: null, url: 168 })).toEqual({
      width: 168,
      source: "url",
    });
  });

  it("reserves nothing in auto mode when no host and no URL value arrived", () => {
    // A plain vite dev page: there is no host to follow, so "follow host" must
    // not invent a padding. Having entered the webapp once before must not
    // change this either, otherwise the mode silently stops meaning what its
    // label says.
    expect(resolveControlsWidth({ mode: "auto", host: null, url: null })).toEqual({
      width: 0,
      source: "none",
    });
  });

  it("simulates the fallback only in always mode", () => {
    expect(resolveControlsWidth({ mode: "always", host: null, url: null })).toEqual({
      width: FALLBACK_CONTROLS_WIDTH,
      source: "simulated",
    });
  });

  it("reserves nothing in never mode, even with a host value", () => {
    expect(resolveControlsWidth({ mode: "never", host: 160, url: 168 })).toEqual({
      width: 0,
      source: "none",
    });
  });
});
