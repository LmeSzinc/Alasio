/**
 * Tests for the header geometry shared by the header itself and every drag
 * region declaration.
 *
 * The numbers must describe the same layout in a session with window controls
 * and in one without: the band's right edge is exactly where the language
 * selector starts, and the header pads the controls on top of its own `px-4`
 * rather than replacing it.
 */
import { describe, expect, it } from "vitest";
import { HEADER_PADDING_X, HEADER_RIGHT_INSET } from "$lib/components/header/geometry";

/** Width of the actions group: language (36) + gap (4) + theme (36) */
const ACTIONS_WIDTH = 36 + 4 + 36;

/** Window width the model below is evaluated at */
const WINDOW = 960;

/**
 * Lays out the right end of the header the way AppHeader and the drag region
 * reporter do.
 *
 * Args:
 *   controls: Reserve width of the window controls
 */
function layout(controls: number) {
  const paddingRight = HEADER_PADDING_X + controls;
  const contentRight = WINDOW - paddingRight;
  const langLeft = contentRight - ACTIONS_WIDTH;
  const bandRight = WINDOW - (HEADER_RIGHT_INSET + controls);
  return {
    gapBandToLang: langLeft - bandRight,
    themeToEdge: WINDOW - contentRight,
  };
}

describe("TestHeaderGeometry", () => {
  it("covers the right padding plus the trailing actions", () => {
    expect(HEADER_RIGHT_INSET).toBe(HEADER_PADDING_X + ACTIONS_WIDTH);
  });

  it("puts the band right edge where the language selector starts", () => {
    // The invariant both constants have to keep, with and without the window
    // controls: no gap, but also no overlap.
    expect(layout(0).gapBandToLang).toBe(0);
    expect(layout(168).gapBandToLang).toBe(0);
  });

  it("keeps the header padding next to the window controls", () => {
    // The padding is added on top of `px-4`, not substituted for it: with no
    // controls the actions sit 16px from the window edge, and with controls the
    // same 16px survives between them and the reserved area. Writing the bare
    // controls width would drop that 16 and shift the actions right by it.
    expect(layout(0).themeToEdge).toBe(HEADER_PADDING_X);
    expect(layout(168).themeToEdge).toBe(HEADER_PADDING_X + 168);
  });
});
