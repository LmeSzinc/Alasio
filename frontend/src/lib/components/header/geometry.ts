// Geometry of the application header (AppHeader.svelte, h-12).
//
// The header is drawn by the frontend, but the electron host overlays its
// window controls on the same bar. A page that wants part of the bar to stay
// clickable declares the drag region itself (see `$lib/window/dragRegion`);
// this is the number every declaration is derived from.

/**
 * Horizontal padding of the header: the `px-4` of AppHeader.
 *
 * The window controls width is added on top of it rather than replacing it: an
 * inline `padding-right` overrides the whole Tailwind padding, so writing the
 * bare controls width would shift the trailing actions right by 16px and leave
 * a gap between them and the drag band.
 */
export const HEADER_PADDING_X = 16;

/**
 * Width AppHeader keeps for its own trailing actions, measured from the
 * content's right edge: px-4 (16) + language selector (36) + gap (4) +
 * theme toggle (36).
 *
 * The host window controls are NOT part of it: they are host-drawn, so the
 * drag region reporter adds `frame.controls.width` on top.
 */
export const HEADER_RIGHT_INSET = 92;
