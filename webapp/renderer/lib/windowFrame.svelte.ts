import { type DragRegion, sanitizeRegion } from "./dragRegionSanitize";

// Geometry the webapp shares with the embedded frontend: the drag region and
// the width of the window controls.
//
// The window is frameless and the frontend owns the visible header, so the
// host draws a transparent drag strip over the iframe. Where that strip may
// live is decided by the frontend (it knows the header layout, and which page
// puts controls there); this module holds the value it reports, plus the
// reserve width of the window controls, which the frontend reads back to keep
// its own header clear of them.

/**
 * Reserve width used until `WindowControls` reports the measured one:
 * hide + minimize + maximize + close (4 * 36) + their gaps (3 * 4) + the right
 * margin (6) + the matching 6px gap on the left of the group.
 */
export const FALLBACK_CONTROLS_WIDTH = 168;

/** Gap kept between the window controls and the content that ends before them */
export const CONTROLS_GAP = 6;

export type { CssLength, DragRegion } from "./dragRegionSanitize";

export const windowFrame = $state<{ controlsWidth: number; region: DragRegion | null }>({
  controlsWidth: FALLBACK_CONTROLS_WIDTH,
  region: null,
});

/**
 * Applies a reported region, keeping the previous one when it is malformed.
 *
 * Args:
 *   raw: Value of the `alasio:drag-region` message
 */
export function applyDragRegion(raw: unknown): void {
  const region = sanitizeRegion(raw);
  if (region === null) return;
  windowFrame.region = region;
}

/** Forgets the reported region: the title bar falls back to its default band */
export function resetDragRegion(): void {
  windowFrame.region = null;
}

/**
 * Updates the measured reserve width of the window controls.
 *
 * Args:
 *   width: Distance from the window's right edge to the left edge of the group
 */
export function setControlsWidth(width: number): void {
  if (!Number.isFinite(width) || width < 0) return;
  windowFrame.controlsWidth = Math.round(width);
}
