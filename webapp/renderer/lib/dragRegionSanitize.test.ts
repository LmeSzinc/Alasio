/**
 * Tests for the validation of the drag region reported by the embedded
 * frontend.
 *
 * This is a trust boundary: the value arrives over `postMessage` from an iframe
 * and is written straight into the title bar's `style` attribute, so anything
 * that is not a plain pixel value or a self-contained CSS expression has to be
 * refused. Dropping a message keeps the previous band, which is why "refuse"
 * means `null` rather than a default.
 */
import { describe, expect, it } from "vitest";
import { sanitizeLength, sanitizeRegion } from "./dragRegionSanitize";

describe("TestSanitizeLength", () => {
  it("accepts a non-negative pixel value where zero is allowed", () => {
    expect(sanitizeLength(0, true)).toBe(0);
    expect(sanitizeLength(81, true)).toBe(81);
    expect(sanitizeLength(48.5, true)).toBe(48.5);
  });

  it("rejects zero where a positive value is required", () => {
    // The band height: a zero-height strip cannot be dragged.
    expect(sanitizeLength(0, false)).toBeNull();
    expect(sanitizeLength(48, false)).toBe(48);
  });

  it("rejects negative and non-finite numbers", () => {
    expect(sanitizeLength(-1, true)).toBeNull();
    expect(sanitizeLength(Number.NaN, true)).toBeNull();
    expect(sanitizeLength(Number.POSITIVE_INFINITY, true)).toBeNull();
  });

  it("accepts a self-contained calc expression", () => {
    expect(sanitizeLength("calc(max(25%, 100% - 384px) + 48px)", true)).toBe("calc(max(25%, 100% - 384px) + 48px)");
    expect(sanitizeLength("min(10px, 2rem)", true)).toBe("min(10px, 2rem)");
  });

  it("rejects an expression that does not start with a math function", () => {
    // A bare length would be fine for a browser but is not what the frontend
    // composes, so it is not part of the contract either.
    expect(sanitizeLength("81px", true)).toBeNull();
    expect(sanitizeLength(" 48px", true)).toBeNull();
  });

  it("rejects named values that reach outside the value", () => {
    expect(sanitizeLength("calc(var(--x) + 1px)", true)).toBeNull();
    expect(sanitizeLength("calc(env(safe-area-inset-right) + 1px)", true)).toBeNull();
    expect(sanitizeLength("calc(attr(data-x) + 1px)", true)).toBeNull();
    expect(sanitizeLength("calc(url(x) + 1px)", true)).toBeNull();
  });

  it("rejects characters that could escape the declaration", () => {
    expect(sanitizeLength("calc(1px); color: red", true)).toBeNull();
    expect(sanitizeLength('calc(1px)"', true)).toBeNull();
    expect(sanitizeLength("calc(1px) /* x */", true)).toBeNull();
    expect(sanitizeLength("calc(1px)\\65", true)).toBeNull();
  });

  it("rejects empty, over-long and non-string values", () => {
    expect(sanitizeLength("", true)).toBeNull();
    expect(sanitizeLength(`calc(${"1px + ".repeat(40)}1px)`, true)).toBeNull();
    expect(sanitizeLength(null, true)).toBeNull();
    expect(sanitizeLength(undefined, true)).toBeNull();
    expect(sanitizeLength({}, true)).toBeNull();
    expect(sanitizeLength([1], true)).toBeNull();
  });
});

describe("TestSanitizeRegion", () => {
  it("accepts the region the frontend reports for a page", () => {
    expect(sanitizeRegion({ height: 48, left: 81, right: 260 })).toEqual({ height: 48, left: 81, right: 260 });
  });

  it("accepts the sheet expression as the right inset", () => {
    const region = { height: 48, left: 81, right: "calc(max(25%, 100% - 384px) + 48px)" };

    expect(sanitizeRegion(region)).toEqual(region);
  });

  it("rejects a missing or malformed inset", () => {
    expect(sanitizeRegion({ height: 48, left: 81 })).toBeNull();
    expect(sanitizeRegion({ height: 48, left: 81, right: -1 })).toBeNull();
    expect(sanitizeRegion({ height: 0, left: 81, right: 260 })).toBeNull();
    expect(sanitizeRegion({ height: "48", left: 81, right: 260 })).toBeNull();
  });

  it("rejects values that are not a region at all", () => {
    expect(sanitizeRegion(null)).toBeNull();
    expect(sanitizeRegion(undefined)).toBeNull();
    expect(sanitizeRegion(48)).toBeNull();
    expect(sanitizeRegion("calc(1px)")).toBeNull();
    expect(sanitizeRegion([])).toBeNull();
  });

  it("drops nothing when an extra field is present", () => {
    // Unknown fields are ignored rather than rejected: a future frontend may
    // send more, and the host only reads the three insets.
    expect(sanitizeRegion({ height: 48, left: 0, right: 92, top: 0 })).toEqual({
      height: 48,
      left: 0,
      right: 92,
    });
  });
});
