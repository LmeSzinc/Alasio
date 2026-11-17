/**
 * Tests for the drag region payload sent to the electron host.
 *
 * The host applies the three insets as CSS (`left` / `right` / `height`), so
 * the payload must stay viewport independent: the only place a viewport value
 * is allowed is a CSS expression the browser evaluates, never a number
 * computed here.
 */
import { describe, expect, it } from "vitest";
import type { DragRegion, DragRegionLayer } from "$lib/window/dragRegion.svelte";
import { isValidRegion, mergeRegionLayers, samePayload, toPayload } from "$lib/window/dragRegion.svelte";

describe("TestMergeRegionLayers", () => {
  it("returns the base region when no layer is declared", () => {
    expect(mergeRegionLayers([])).toEqual({ height: 48, left: 0, right: 0 });
  });

  it("merges only the sides a layer declares", () => {
    const layout: DragRegionLayer = () => ({ left: 96, right: 92 });
    const page: DragRegionLayer = () => ({ right: 352 });

    expect(mergeRegionLayers([layout, page])).toEqual({ height: 48, left: 96, right: 352 });
  });

  it("skips a layer that contributes nothing", () => {
    const layout: DragRegionLayer = () => ({ left: 96 });
    const sheetClosed: DragRegionLayer = () => null;

    expect(mergeRegionLayers([layout, sheetClosed])).toEqual({ height: 48, left: 96, right: 0 });
  });

  it("lets a layer narrow the region declared below it", () => {
    const seen: DragRegion[] = [];
    const layout: DragRegionLayer = () => ({ left: 96 });
    const page: DragRegionLayer = (inherited) => {
      seen.push({ ...inherited });
      return { right: 200 };
    };

    expect(mergeRegionLayers([layout, page]).left).toBe(96);
    expect(seen).toEqual([{ height: 48, left: 96, right: 0 }]);
  });
});

describe("TestToPayload", () => {
  it("adds the window controls width to the right inset", () => {
    // 92 = header actions, +168 = the controls the host draws over the header
    expect(toPayload({ height: 48, left: 96, right: 92 }, 168)).toEqual({ height: 48, left: 96, right: 260 });
  });

  it("keeps a css expression and lets the browser add the controls width", () => {
    const payload = toPayload({ height: 48, left: 96, right: "max(25%, 100% - 384px)" }, 168);

    expect(payload.right).toBe("calc(max(25%, 100% - 384px) + 168px)");
  });

  it("adds inside an existing calc instead of nesting a second one", () => {
    // The sheet layer composes a calc() itself; wrapping it would produce
    // calc(calc(...) + 168px), which is valid but needlessly nested.
    const payload = toPayload({ height: 48, left: 93, right: "calc(max(25%, 100% - 384px) + 48px - 168px)" }, 168);

    expect(payload.right).toBe("calc(max(25%, 100% - 384px) + 48px - 168px + 168px)");
  });

  it("does not change the right inset when no controls are reserved", () => {
    expect(toPayload({ height: 48, left: 0, right: 92 }, 0)).toEqual({ height: 48, left: 0, right: 92 });
  });
});

describe("TestIsValidRegion", () => {
  it("accepts pixel insets and a full height band", () => {
    expect(isValidRegion({ height: 48, left: 96, right: 260 })).toBe(true);
  });

  it("accepts a css expression as the right inset", () => {
    expect(isValidRegion({ height: 48, left: 96, right: "calc(25% + 48px)" })).toBe(true);
  });

  it("rejects a zero or negative height", () => {
    expect(isValidRegion({ height: 0, left: 0, right: 0 })).toBe(false);
    expect(isValidRegion({ height: -48, left: 0, right: 0 })).toBe(false);
  });

  it("rejects negative or non finite insets", () => {
    expect(isValidRegion({ height: 48, left: -1, right: 0 })).toBe(false);
    expect(isValidRegion({ height: 48, left: 0, right: Number.NaN })).toBe(false);
  });

  it("rejects an empty expression", () => {
    expect(isValidRegion({ height: 48, left: 0, right: "" })).toBe(false);
  });
});

describe("TestSamePayload", () => {
  it("treats two identical regions as unchanged", () => {
    expect(samePayload({ height: 48, left: 96, right: 260 }, { height: 48, left: 96, right: 260 })).toBe(true);
  });

  it("tells nothing-sent apart from a cleared region", () => {
    expect(samePayload(null, undefined)).toBe(false);
    expect(samePayload(null, null)).toBe(true);
  });

  it("detects a changed inset", () => {
    expect(samePayload({ height: 48, left: 96, right: 260 }, { height: 48, left: 96, right: 352 })).toBe(false);
  });
});
