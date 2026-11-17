// Drag region reported to the electron host.
//
// The webapp is frameless and overlays a transparent drag strip over the
// iframe header, but `-webkit-app-region` only applies to the host document:
// an iframe cannot make its own header draggable. So the host draws the strip,
// and the frontend tells it where the strip may live -- one band at the top of
// the window, expressed as three insets:
//
//   height: band height, the band starts at y = 0
//   left:   distance from the window's left edge
//   right:  distance from the window's right edge, excluding the host window
//           controls (the reporter adds their width, see toPayload())
//
// None of them depends on the viewport width, so the browser recomputes the
// geometry itself on every resize: dragging or resizing the window runs no
// code here and sends no message.
//
// Layering: a layout declares its band, a page that puts controls in the
// header narrows it, and `createStackContext` drops the page layer when the
// page is destroyed (see `$lib/context/stack.svelte`).
import { browser } from "$app/environment";
import { type StackHandle, createStackContext } from "$lib/context/stack.svelte";
import { isElectron, isElectronSession } from "$lib/use/useElectronEnv.svelte";
import { frame } from "$lib/window/frame.svelte";
import { readStored, writeStored } from "$lib/window/storage";

/** A CSS length: a number of pixels, or an expression the browser evaluates */
export type CssLength = number | string;

export interface DragRegion {
  height: CssLength;
  left: CssLength;
  right: CssLength;
}

/**
 * One layer of the drag region stack.
 *
 * Args:
 *   inherited: Region declared by the layers below, the layer may narrow it
 *
 * Returns:
 *   The insets this layer overrides, or `null` to contribute nothing
 */
export type DragRegionLayer = (inherited: DragRegion) => Partial<DragRegion> | null;

/**
 * The band covering the whole window top, used when no layer declares
 * anything. 48 is the AppHeader height (`h-12`); the layers below only narrow
 * the sides, so the height is declared once here.
 */
const BASE_REGION: DragRegion = { height: 48, left: 0, right: 0 };

/**
 * Folds the layers bottom-up: the base region, then each layer over it.
 *
 * Args:
 *   layers: Layers in mount order
 */
export function mergeRegionLayers(layers: readonly DragRegionLayer[]): DragRegion {
  let region = BASE_REGION;
  for (const layer of layers) {
    const part = layer(region);
    if (part) region = { ...region, ...part };
  }
  return region;
}

export const DragRegionContext = createStackContext<DragRegionLayer, DragRegion>(
  "drag_region_stack",
  mergeRegionLayers,
);

/**
 * Converts a declared region to the insets the host applies.
 *
 * The host window controls sit inside the band's right inset, so their width
 * is added here: pages declare up to their own content, not up to the buttons
 * the host draws.
 *
 * Args:
 *   region: Region declared by the layers
 *   controlsWidth: Reserve width of the host window controls
 */
export function toPayload(region: DragRegion, controlsWidth: number): DragRegion {
  return {
    height: region.height,
    left: region.left,
    right: addControls(region.right, controlsWidth),
  };
}

/**
 * Adds the window controls width to a right inset.
 *
 * Numbers are plain pixels. An expression is a `calc()` the layer composed
 * (the sheet panel is a percentage of the window), so the addition goes inside
 * it rather than wrapping it in a second `calc()`.
 *
 * Args:
 *   right: Right inset declared by a layer
 *   controlsWidth: Reserve width of the host window controls
 */
function addControls(right: CssLength, controlsWidth: number): CssLength {
  if (typeof right === "number") return right + controlsWidth;
  const match = /^calc\((.*)\)$/s.exec(right.trim());
  return match ? `calc(${match[1]} + ${controlsWidth}px)` : `calc(${right} + ${controlsWidth}px)`;
}

/** Whether a region is worth sending; a broken layer must not clear the host */
export function isValidRegion(region: DragRegion): boolean {
  const isLength = (value: CssLength) =>
    typeof value === "number" ? Number.isFinite(value) && value >= 0 : value.length > 0;
  return (
    typeof region.height === "number" &&
    Number.isFinite(region.height) &&
    region.height > 0 &&
    isLength(region.left) &&
    isLength(region.right)
  );
}

/** Whether two payloads describe the same band, `undefined` = nothing sent yet */
export function samePayload(a: DragRegion | null | undefined, b: DragRegion | null | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.height === b.height && a.left === b.left && a.right === b.right;
}

/** Last payload computed here, read by the dev outline and the dev tools */
export const dragRegionState = $state<{ current: DragRegion | null }>({ current: null });

const OUTLINE_KEY = "alasio-drag-region-outline";

/** Dev-only: draw the reported band over the page (see DragRegionOutline.svelte) */
export const dragRegionOutline = $state<{ enabled: boolean }>({
  enabled: browser ? readStored(OUTLINE_KEY, false) : false,
});

/**
 * Switches the dev outline.
 *
 * Args:
 *   enabled: Whether the band is drawn over the page
 */
export function setDragRegionOutline(enabled: boolean) {
  dragRegionOutline.enabled = enabled;
  writeStored(OUTLINE_KEY, enabled);
}

/**
 * Starts reporting the drag region of the stack.
 *
 * Computes the payload on every change of the stack or of the window controls
 * width and sends it to the host, skipping duplicates. Nothing is sent while
 * the page is being resized: the insets do not depend on the viewport.
 *
 * Args:
 *   handle: Handle of the drag region stack, from `DragRegionContext.init()`
 *
 * Returns:
 *   Stops the reporter
 */
export function startDragRegionReporter(handle: StackHandle<DragRegion>): () => void {
  // A remote visitor has no host and no dev tool to feed.
  if (!browser || (!import.meta.env.DEV && !isElectronSession)) {
    return () => {};
  }

  // `undefined` = nothing sent yet, `null` = the stack was emptied
  let sent: DragRegion | null | undefined = undefined;

  return $effect.root(() => {
    $effect(() => {
      const region = handle.size > 0 ? handle.value : null;
      if (region && !isValidRegion(region)) {
        // A broken declaration keeps the host on the last good region
        return;
      }

      const payload = region ? toPayload(region, frame.controls.width) : null;
      dragRegionState.current = payload;

      if (!isElectron.value) return;
      if (samePayload(payload, sent)) return;
      // Never declared: the host default (whole bar minus the controls) already applies
      if (payload === null && sent === undefined) return;

      sent = payload;
      window.parent.postMessage({ type: "alasio:drag-region", region: payload }, "*");
    });
  });
}
