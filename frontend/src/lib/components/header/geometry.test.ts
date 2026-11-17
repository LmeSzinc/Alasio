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
import {
  HEADER_ACTIONS_WIDTH,
  HEADER_PADDING_X,
  bandRightInset,
  headerPaddingRight,
} from "$lib/components/header/geometry";

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
  const contentRight = WINDOW - headerPaddingRight(controls);
  const langLeft = contentRight - HEADER_ACTIONS_WIDTH;
  const bandRight = WINDOW - (bandRightInset(controls) + controls);
  // The controls group is 4 * 36 + 3 * 4 + 6 wide, flush to the window edge.
  const controlsLeft = WINDOW - (4 * 36 + 3 * 4 + 6);
  return {
    gapBandToLang: langLeft - bandRight,
    gapActionsToControls: controls > 0 ? controlsLeft - contentRight : null,
  };
}

describe("TestHeaderGeometry", () => {
  it("keeps the px-4 padding only when there are no window controls", () => {
    expect(headerPaddingRight(0)).toBe(HEADER_PADDING_X);
    expect(headerPaddingRight(168)).toBe(168);
  });

  it("puts the band right edge where the language selector starts", () => {
    // The invariant both helpers have to keep, with and without the controls:
    // no gap, but also no overlap.
    expect(layout(0).gapBandToLang).toBe(0);
    expect(layout(168).gapBandToLang).toBe(0);
  });

  it("leaves the same 6px between the actions and the controls as inside the group", () => {
    // The reserve width already contains that gap; adding the padding on top of
    // it (instead of replacing it) put 22px there instead of 6.
    expect(layout(168).gapActionsToControls).toBe(6);
  });
});
