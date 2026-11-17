// Geometry of the application header (AppHeader.svelte, h-12).
//
// The header is drawn by the frontend, but the electron host overlays its
// window controls on the same bar. A page that wants part of the bar to stay
// clickable declares the drag region itself (see `$lib/window/dragRegion`);
// these are the numbers every declaration is derived from.

/** Horizontal padding of the header: the `px-4` of AppHeader */
export const HEADER_PADDING_X = 16;

/** Width of the trailing actions group: language (36) + gap (4) + theme (36) */
export const HEADER_ACTIONS_WIDTH = 36 + 4 + 36;

/**
 * Right padding the header must apply for a given reserve width.
 *
 * The reserve width the host reports already includes everything between the
 * content and the window edge: the buttons, their gaps, the 6px margin on the
 * right and the matching 6px gap on their left. So it *replaces* the `px-4`
 * padding instead of being added to it — adding would push the trailing actions
 * 16px further from the buttons than the client draws them.
 *
 * Without a host there are no buttons, so the plain `px-4` padding applies.
 *
 * Args:
 *   controlsWidth: Reserve width of the window controls, 0 in a browser
 */
export function headerPaddingRight(controlsWidth: number): number {
  return controlsWidth > 0 ? controlsWidth : HEADER_PADDING_X;
}

/**
 * Right inset of the drag band, measured from the window edge.
 *
 * The band has to end exactly where the language selector starts, which is:
 *
 *     window - headerPaddingRight - actions
 *
 * and the reporter adds the controls width on top of what a layer declares, so
 * the layer value is that expression minus the controls width. With the
 * controls the padding and the reserve cancel out, leaving the actions width
 * alone; without them the `px-4` padding stays in.
 *
 * Args:
 *   controlsWidth: Reserve width of the window controls, 0 in a browser
 */
export function bandRightInset(controlsWidth: number): number {
  return headerPaddingRight(controlsWidth) - controlsWidth + HEADER_ACTIONS_WIDTH;
}
