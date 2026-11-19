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
import { HEADER_ACTIONS_WIDTH, bandRightInset } from "$lib/components/header/geometry";
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
export function defaultRegion(breakpoint: Breakpoint, controlsWidth = 0): DragRegionLayer {
  return () => ({ left: DEFAULT_BAND_LEFT[breakpoint], right: bandRightInset(controlsWidth) });
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
export function authRegion(controlsWidth = 0): DragRegionLayer {
  return () => ({ left: 0, right: bandRightInset(controlsWidth) });
}

/** Width of the mod selector in a compact dev header: `w-30` = 120px */
export const MOD_SELECTOR_WIDTH = 120;

/**
 * Classes that give a compact header's mod selector its width.
 *
 * `w-30` is 30 steps of the 4px spacing scale, which is `MOD_SELECTOR_WIDTH`:
 * the drag band reserves exactly this width, so the two have to change
 * together (a test asserts it). `max-w-1/2` caps the selector at half of the
 * header row, which is what guarantees the title the other half at any window
 * width.
 *
 * `min-w-0` and no `shrink-0` are what keep a narrow window from overflowing:
 * the fixed parts of the header (menu button, language, theme, window controls)
 * already take most of a 480px window, so the selector has to give way. Without
 * it the header row's min-content is wider than the viewport and the whole
 * right end of the header is pushed off screen.
 */
export const MOD_SELECTOR_CLASS = "w-30 max-w-1/2 min-w-0";

/**
 * Width the compact header cluster takes from the right edge of the header
 * content box: the selector plus the gap the header puts before it.
 *
 * The selector is the last thing of the page snippet, so what the band has to
 * clear is the selector itself plus the `gap-1` between the snippet and the
 * language selector.
 */
export const MOD_SELECTOR_CLUSTER = 4 + MOD_SELECTOR_WIDTH;

/**
 * Header band of a compact dev page, which puts the mod selector in the header.
 *
 * Only the right side is narrowed: the left stays what the layout declared, so
 * the aside config column is still clickable. The title of the page is plain
 * text, so it can stay under the band.
 *
 * Args:
 *   controlsWidth: Reserve width of the host window controls
 *   clusterWidth: Width of the selector cluster, for a page whose selector is
 *     narrower than the default (the 50% cap on small screens)
 */
export function modSelectorRegion(controlsWidth = 0, clusterWidth = MOD_SELECTOR_CLUSTER): DragRegionLayer {
  return () => ({ right: bandRightInset(controlsWidth) + clusterWidth });
}
