/**
 * Tests for the card width shared by the config pages and the dev pages.
 */
import { describe, expect, it } from "vitest";
import { cardWidth } from "$lib/use/cardWidth.svelte";

describe("TestCardWidth", () => {
  it("follows the parent width until max-w-180", () => {
    expect(cardWidth(0)).toBe("max-w-180");
    expect(cardWidth(320)).toBe("max-w-180");
    expect(cardWidth(720)).toBe("max-w-180");
    expect(cardWidth(1199)).toBe("max-w-180");
  });

  it("keeps the 3/5 ratio of the parent from 1200 to 1600", () => {
    expect(cardWidth(1200)).toBe("w-3/5");
    expect(cardWidth(1400)).toBe("w-3/5");
    expect(cardWidth(1599)).toBe("w-3/5");
  });

  it("stops at max-w-240 on a wide parent", () => {
    expect(cardWidth(1600)).toBe("max-w-240");
    expect(cardWidth(2560)).toBe("max-w-240");
  });
});
