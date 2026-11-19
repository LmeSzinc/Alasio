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
import { HEADER_ACTIONS_WIDTH, bandRightInset, headerPaddingRight } from "$lib/components/header/geometry";
import { type DragRegion, type DragRegionLayer, mergeRegionLayers, toPayload } from "$lib/window/dragRegion.svelte";
import {
  DEFAULT_BAND_LEFT,
  MOD_SELECTOR_CLASS,
  MOD_SELECTOR_WIDTH,
  authRegion,
  breakpointOf,
  defaultRegion,
  modSelectorRegion,
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
  return windowWidth - headerPaddingRight(controls) - HEADER_ACTIONS_WIDTH;
}

describe("TestDefaultRegion", () => {
  it.each(CONTROLS)("starts at the menu button on small screens (controls %i)", (controls) => {
    const width = WIDTHS.sm;
    const payload = payloadOf([defaultRegion(breakpointOf(width), controls)], controls);

    // px-4 (16) + size-9 (36) + gap-1 (4): the button spans 16..52, so 56 clears it
    expect(payload.left).toBe(56);
    expect(payload.height).toBe(48);
  });

  it.each(CONTROLS)("starts at the aside config column on medium screens (controls %i)", (controls) => {
    const width = WIDTHS.md;
    const payload = payloadOf([defaultRegion(breakpointOf(width), controls)], controls);

    // The config column is w-20 (80) + 1px border; the nav column and the
    // header sit right of it and are draggable.
    expect(payload.left).toBe(81);
  });

  it.each(CONTROLS)("starts at the window edge on large screens (controls %i)", (controls) => {
    const width = WIDTHS.xl;
    const payload = payloadOf([defaultRegion(breakpointOf(width), controls)], controls);

    // The aside floats 16px below the top there, so nothing is under the band.
    expect(payload.left).toBe(0);
  });

  it.each(CONTROLS)("ends on the language selector at every breakpoint (controls %i)", (controls) => {
    for (const width of Object.values(WIDTHS)) {
      const payload = payloadOf([defaultRegion(breakpointOf(width), controls)], controls);

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
    const payload = payloadOf([authRegion(controls)], controls);

    expect(payload.left).toBe(0);
    expect(bandRight(payload, width)).toBe(langLeft(width, controls));
  });
});

describe("TestModSelectorRegion", () => {
  it("keeps the selector class and the reserved width in step", () => {
    // The band reserves MOD_SELECTOR_WIDTH; the header gives the selector that
    // width with a utility class. Changing one without the other would either
    // cover the control or leave a dead strip beside it, and neither shows up
    // in a type check.
    const step = /(?:^| )w-(\d+)(?: |$)/.exec(MOD_SELECTOR_CLASS)?.[1];
    if (step === undefined) throw new Error(`no w-<n> class in ${MOD_SELECTOR_CLASS}`);

    expect(Number(step) * 4).toBe(MOD_SELECTOR_WIDTH);
  });

  it("caps the selector so the title always keeps half the row", () => {
    // The cap is what lets the selector stay in the header at every width: the
    // title of a compact page is only shown there.
    expect(MOD_SELECTOR_CLASS.split(" ")).toContain("max-w-1/2");
  });
  it.each(CONTROLS)("stops left of the header selector (controls %i)", (controls) => {
    const width = WIDTHS.xl;
    // Page layer over the private layout layer, both pushed.
    const payload = payloadOf([defaultRegion(breakpointOf(width), controls), modSelectorRegion(controls)], controls);

    // Actions (76) + gap-1 (4) + ModSelector w-30 (120) = 200 from the content
    // edge; the selector itself then spans up to the actions group.
    expect(payload.right).toBe(bandRightInset(controls) + 4 + MOD_SELECTOR_WIDTH + controls);
    const selectorRight = width - headerPaddingRight(controls) - HEADER_ACTIONS_WIDTH - 4;
    const selectorLeft = selectorRight - MOD_SELECTOR_WIDTH;
    expect(bandRight(payload, width)).toBe(selectorLeft);
  });

  it.each(CONTROLS)("keeps the layout's left inset (controls %i)", (controls) => {
    for (const width of Object.values(WIDTHS)) {
      const breakpoint = breakpointOf(width);
      const payload = payloadOf([defaultRegion(breakpoint, controls), modSelectorRegion(controls)], controls);

      // The page narrows the right side only; the aside must stay clickable.
      expect(payload.left).toBe(DEFAULT_BAND_LEFT[breakpoint]);
    }
  });

  it("leaves a draggable middle on medium screens with a host", () => {
    // The compact dev pages drop the nav column, so the band starts at the
    // config column and runs to the selector: 511px at the default 960 window,
    // which is the size the window opens with.
    const width = 960;
    const controls = 168;
    const payload = payloadOf([defaultRegion(breakpointOf(width), controls), modSelectorRegion(controls)], controls);

    expect(bandRight(payload, width) - Number(payload.left)).toBe(511);
  });

  it.each(CONTROLS)("accepts a narrower cluster for a capped selector (controls %i)", (controls) => {
    // A page whose selector is capped below 120px (the 50% limit of a narrow
    // header) passes its measured width, so the band never covers the control.
    const width = 480;
    const cluster = 4 + 88;
    const payload = payloadOf(
      [defaultRegion(breakpointOf(width), controls), modSelectorRegion(controls, cluster)],
      controls,
    );

    const expected = width - (bandRightInset(controls) + cluster + controls) - DEFAULT_BAND_LEFT.sm;
    expect(bandRight(payload, width) - Number(payload.left)).toBe(expected);
  });
});

describe("TestRegionInvariants", () => {
  it("keeps the band positive at every breakpoint and both sessions", () => {
    for (const controls of CONTROLS) {
      for (const width of Object.values(WIDTHS)) {
        const payload = payloadOf([defaultRegion(breakpointOf(width), controls)], controls);
        const rendered = bandRight(payload, width) - Number(payload.left);

        expect(rendered).toBeGreaterThan(0);
      }
    }
  });

  it("reserves only the controls width difference between the two sessions", () => {
    const width = WIDTHS.md;
    const withHost = payloadOf([defaultRegion(breakpointOf(width), 168)], 168);
    const withoutHost = payloadOf([defaultRegion(breakpointOf(width), 0)], 0);

    // The header content is laid out the same way in both sessions, but the
    // band edge follows the content: with the controls the reserve width
    // replaces the px-4 padding, so the edge moves by the controls minus that
    // padding. Without controls the actions sit at the plain px-4 padding.
    expect(Number(withHost.right) - Number(withoutHost.right)).toBe(168 - 16);
    expect(bandRight(withoutHost, width) - bandRight(withHost, width)).toBe(168 - 16);
    expect(withoutHost.left).toBe(withHost.left);
  });
});
