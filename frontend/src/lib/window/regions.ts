// Drag region declarations per breakpoint and page.
//
// Kept out of the components so the numbers can be asserted: they were wrong
// twice before (a left edge that cut through the config column, a right edge
// that lost the header padding) and nothing caught it.
//
// Every export is a stack layer: it receives the region the layers below
// declared and narrows it. Insets are relative to the header content box; the
// reporter adds the host window controls width to `right` (see `toPayload`), so
// a layer that wants a viewport-relative edge subtracts it back out -- the sheet
// is the only such case, because its close button is positioned from the panel's
// edge.
import { HEADER_RIGHT_INSET } from "$lib/components/header/geometry";
import type { DragRegionLayer } from "./dragRegion.svelte";

/** Breakpoints of the application layout: <768, 768-1279, >=1280 */
export type Breakpoint = "sm" | "md" | "xl";

/**
 * Left inset of the default header band, per breakpoint.
 *
 * - sm (56): the menu button, px-4 (16) + size-9 (36) + gap-1 (4)
 * - md (81): the aside config column, w-20 (80) + its 1px right border; the
 *   band then covers the nav column and the header, and the config list stays
 *   clickable
 * - xl (0): the aside is a floating card with a 16px top margin, so the band
 *   has only the page background under it
 */
export const DEFAULT_BAND_LEFT: Record<Breakpoint, number> = { sm: 56, md: 81, xl: 0 };

/** Breakpoint the current window width belongs to */
export function breakpointOf(width: number): Breakpoint {
  if (width >= 1280) return "xl";
  if (width >= 768) return "md";
  return "sm";
}

/** Default header band of the private layout */
export function defaultRegion(breakpoint: Breakpoint): DragRegionLayer {
  return () => ({ left: DEFAULT_BAND_LEFT[breakpoint], right: HEADER_RIGHT_INSET });
}

/**
 * Band of the private layout while the sheet is open (<768).
 *
 * The panel is min(75vw, 24rem) wide and its close button sits 48px inside the
 * panel's right edge, so the band ends there: `max(25%, 100% - 384px) + 48px`
 * measured from the window's right edge. That edge is viewport-relative, hence
 * the subtraction of the controls width the reporter is about to add.
 *
 * Args:
 *   controlsWidth: Reserve width of the host window controls
 */
export function sheetRegion(controlsWidth: number): DragRegionLayer {
  return () => ({
    left: DEFAULT_BAND_LEFT.md,
    right: `calc(max(25%, 100% - 384px) + 48px - ${controlsWidth}px)`,
  });
}

/** Header band of the public layouts: no aside, no menu button */
export const authRegion: DragRegionLayer = () => ({ left: 0, right: HEADER_RIGHT_INSET });

/** Width of the interactive cluster in the `/dev/assets` header: gap-1 (4) + ModSelector w-64 (256) */
export const DEV_ASSETS_CLUSTER_WIDTH = 4 + 256;

/**
 * Header band of `/dev/assets`, which puts a selector in the header.
 *
 * Only the right side is narrowed: the left stays what the layout declared, so
 * the aside config column is still clickable.
 */
export const devAssetsRegion: DragRegionLayer = () => ({
  right: HEADER_RIGHT_INSET + DEV_ASSETS_CLUSTER_WIDTH,
});
