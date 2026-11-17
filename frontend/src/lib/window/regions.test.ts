/**
 * Tests for every drag region declaration: each page a user can land on, with
 * and without the host window controls.
 *
 * The band is what the host turns into a draggable strip, so each case checks
 * the geometry a user would feel:
 *
 * - the left edge clears whatever must stay clickable (menu button, aside
 *   config column) and nothing more
 * - the right edge lands exactly on the language selector, leaving no gap and
 *   no overlap, whether or not the controls reserve space
 * - the height is the header height
 *
 * "Without the host" is `controls = 0` (a plain browser); "with the host" is the
 * real reserve width. Both go through the same merge/reporter pipeline the app
 * uses, so a broken layer or a broken `toPayload` fails here.
 */
import { describe, expect, it } from "vitest";
import { HEADER_PADDING_X, HEADER_RIGHT_INSET } from "$lib/components/header/geometry";
import { type DragRegion, type DragRegionLayer, mergeRegionLayers, toPayload } from "$lib/window/dragRegion.svelte";
import {
  DEFAULT_BAND_LEFT,
  authRegion,
  breakpointOf,
  defaultRegion,
  devAssetsRegion,
  sheetRegion,
} from "$lib/window/regions";

/** Reserve width of the host controls, and the browser case */
const CONTROLS = [0, 168];

/** Width of the language + theme actions group: 36 + gap 4 + 36 */
const ACTIONS = 76;

/** Window widths used per breakpoint */
const WIDTHS = { sm: 700, md: 900, xl: 1400 } as const;

/** Folds layers the way the app does, then converts to the host payload */
function payloadOf(layers: DragRegionLayer[], controls: number): DragRegion {
  return toPayload(mergeRegionLayers(layers), controls);
}

/** Right edge of the band in window coordinates, for a pixel `right` inset */
function bandRight(region: DragRegion, windowWidth: number): number {
  if (typeof region.right !== "number") throw new Error(`expected a pixel inset, got ${region.right}`);
  return windowWidth - region.right;
}

/**
 * Evaluates a CSS `calc()`/`max()` inset the way the browser would, so the test
 * asserts the geometry rather than the text of the expression.
 *
 * Args:
 *   expression: Inset produced by a layer, e.g. `calc(max(25%, 100% - 384px) + 48px)`
 *   windowWidth: Width the percentages resolve against
 */
function evalInset(expression: number | string, windowWidth: number): number {
  if (typeof expression === "number") return expression;
  const body = expression.replace(/^calc\(([\s\S]*)\)$/, "$1");
  const resolved = body
    .replace(/(\d+(?:\.\d+)?)%/g, (_m, pct) => String((windowWidth * Number(pct)) / 100))
    .replace(/(\d+(?:\.\d+)?)px/g, "$1");
  const evaluate = new Function("Math", "max", "min", "clamp", `return ${resolved};`) as (
    m: Math,
    max: Math["max"],
    min: Math["min"],
    clamp: (v: number, lo: number, hi: number) => number,
  ) => number;
  return evaluate(Math, Math.max, Math.min, (v, lo, hi) => Math.min(Math.max(v, lo), hi));
}

/** Left edge of the language selector, given the header padding */
function langLeft(windowWidth: number, controls: number): number {
  return windowWidth - (HEADER_PADDING_X + controls) - ACTIONS;
}

describe("TestDefaultRegion", () => {
  it.each(CONTROLS)("starts at the menu button on small screens (controls %i)", (controls) => {
    const width = WIDTHS.sm;
    const payload = payloadOf([defaultRegion(breakpointOf(width))], controls);

    // px-4 (16) + size-9 (36) + gap-1 (4): the button spans 16..52, so 56 clears it
    expect(payload.left).toBe(56);
    expect(payload.height).toBe(48);
  });

  it.each(CONTROLS)("starts at the aside config column on medium screens (controls %i)", (controls) => {
    const width = WIDTHS.md;
    const payload = payloadOf([defaultRegion(breakpointOf(width))], controls);

    // The config column is w-20 (80) + 1px border; the nav column and the
    // header sit right of it and are draggable.
    expect(payload.left).toBe(81);
  });

  it.each(CONTROLS)("starts at the window edge on large screens (controls %i)", (controls) => {
    const width = WIDTHS.xl;
    const payload = payloadOf([defaultRegion(breakpointOf(width))], controls);

    // The aside floats 16px below the top there, so nothing is under the band.
    expect(payload.left).toBe(0);
  });

  it.each(CONTROLS)("ends on the language selector at every breakpoint (controls %i)", (controls) => {
    for (const width of Object.values(WIDTHS)) {
      const payload = payloadOf([defaultRegion(breakpointOf(width))], controls);

      expect(bandRight(payload, width)).toBe(langLeft(width, controls));
    }
  });

  it("maps widths to the breakpoints the layout uses", () => {
    expect(breakpointOf(767)).toBe("sm");
    expect(breakpointOf(768)).toBe("md");
    expect(breakpointOf(1279)).toBe("md");
    expect(breakpointOf(1280)).toBe("xl");
  });
});

describe("TestSheetRegion", () => {
  it.each(CONTROLS)("ends at the close button's left edge, whatever the controls reserve (controls %i)", (controls) => {
    // The panel is min(75vw, 24rem) wide; the close button sits 48px inside its
    // right edge. A viewport-relative edge must not move with the controls, so
    // the layer subtracts the width the reporter is about to add.
    const width = 767;
    const payload = payloadOf([sheetRegion(controls)], controls);
    const closeLeft = Math.min(width * 0.75, 384) - 48;

    expect(evalInset(payload.right, width)).toBe(width - closeLeft);
  });

  it.each(CONTROLS)("clears the config column of the panel (controls %i)", (controls) => {
    const payload = payloadOf([sheetRegion(controls)], controls);

    // Same two columns as the md layout: the config list is 0..81.
    expect(payload.left).toBe(81);
  });

  it("crosses the close button at the sheet breakpoint", () => {
    // The panel is capped at 384px, so the close button's left edge is fixed
    // once the window is wide enough; below that the panel is 75% of the window.
    const layer = sheetRegion(0);
    const declared = layer({ height: 48, left: 0, right: 0 });
    if (!declared || declared.right === undefined) throw new Error("the sheet layer must declare a right inset");

    for (const width of [400, 600, 700, 767]) {
      const panel = Math.min(width * 0.75, 384);
      const closeLeft = panel - 48;

      expect(evalInset(declared.right, width)).toBe(width - closeLeft);
    }
  });
});

describe("TestAuthRegion", () => {
  it.each(CONTROLS)("covers the whole header up to the language selector (controls %i)", (controls) => {
    const width = 900;
    const payload = payloadOf([authRegion], controls);

    expect(payload.left).toBe(0);
    expect(bandRight(payload, width)).toBe(langLeft(width, controls));
  });
});

describe("TestDevAssetsRegion", () => {
  it.each(CONTROLS)("stops left of the header selector (controls %i)", (controls) => {
    const width = WIDTHS.xl;
    // Page layer over the private layout layer, both pushed.
    const payload = payloadOf([defaultRegion(breakpointOf(width)), devAssetsRegion], controls);

    // Actions (92) + gap-1 (4) + ModSelector w-64 (256) = 352 from the content
    // edge; the selector itself then spans up to the actions group.
    expect(payload.right).toBe(352 + controls);
    const selectorRight = width - (HEADER_RIGHT_INSET + controls);
    const selectorLeft = selectorRight - 256;
    expect(bandRight(payload, width)).toBe(selectorLeft - 4);
  });

  it.each(CONTROLS)("keeps the layout's left inset (controls %i)", (controls) => {
    for (const width of Object.values(WIDTHS)) {
      const breakpoint = breakpointOf(width);
      const payload = payloadOf([defaultRegion(breakpoint), devAssetsRegion], controls);

      // The page narrows the right side only; the aside must stay clickable.
      expect(payload.left).toBe(DEFAULT_BAND_LEFT[breakpoint]);
    }
  });
});

describe("TestRegionInvariants", () => {
  it("keeps the band positive at every breakpoint and both sessions", () => {
    for (const controls of CONTROLS) {
      for (const width of Object.values(WIDTHS)) {
        const payload = payloadOf([defaultRegion(breakpointOf(width))], controls);
        const rendered = bandRight(payload, width) - Number(payload.left);

        expect(rendered).toBeGreaterThan(0);
      }
    }
  });

  it("reserves only the controls width difference between the two sessions", () => {
    const width = WIDTHS.md;
    const withHost = payloadOf([defaultRegion(breakpointOf(width))], 168);
    const withoutHost = payloadOf([defaultRegion(breakpointOf(width))], 0);

    // The page layout is identical in both sessions; only the room the overlay
    // takes on the right differs, by exactly the reserved width.
    expect(Number(withHost.right) - Number(withoutHost.right)).toBe(168);
    expect(bandRight(withoutHost, width) - bandRight(withHost, width)).toBe(168);
    expect(withoutHost.left).toBe(withHost.left);
  });
});
