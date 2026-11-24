/**
 * Tests for the browser-compatibility PostCSS pipeline (postcss.config.ts).
 *
 * The pipeline lowers Tailwind v4 output to Chromium 108, the engine of the
 * packaged Electron. A regression here is silent: the build succeeds and the
 * stylesheet looks reasonable, but a declaration the engine cannot parse is
 * dropped at computed-value time and the element paints nothing. Gradients
 * broke exactly that way, so the hint removal is pinned here.
 */
import postcss from "postcss";
import { describe, expect, it } from "vitest";
import config from "../postcss.config.ts";

/**
 * Runs the real plugin pipeline over a stylesheet.
 *
 * Args:
 *     css (str): Stylesheet to compile
 *
 * Returns:
 *     str: Compiled stylesheet
 */
async function compile(css: string): Promise<string> {
  const result = await postcss(config.plugins).process(css, { from: "test.css" });
  return result.css;
}

describe("TestGradientInterpolation", () => {
  it.each([
    ["to right in oklab", "to right"],
    ["to left in oklab", "to left"],
    ["to bottom in oklab", "to bottom"],
    ["to top in oklab", "to top"],
    ["to bottom right in oklab", "to bottom right"],
    ["to top left in oklch", "to top left"],
    ["45deg in oklab", "45deg"],
    ["to right in oklch longer hue", "to right"],
  ])("removes the hint from %s", async (position, expected) => {
    // Tailwind keeps the position of a gradient, hint included, in this
    // variable; Chromium 108 rejects the hint and paints nothing at all
    const css = `.a{--tw-gradient-position:${position}}`;

    expect((await compile(css)).trim()).toBe(`.a{--tw-gradient-position:${expected}}`);
  });

  it("removes the hint from the @supports redefinition Tailwind emits", async () => {
    // Tailwind writes the plain position first and repeats it with the hint
    // inside an @supports block. The guard itself reports support in engines
    // that then paint nothing, so both declarations have to end up plain.
    const css = [
      ".bg-linear-to-br{--tw-gradient-position:to bottom right}",
      "@supports (background-image:linear-gradient(in lab, red, red))",
      "{.bg-linear-to-br{--tw-gradient-position:to bottom right in oklab}}",
    ].join("");

    expect(await compile(css)).toBe(
      [
        ".bg-linear-to-br{--tw-gradient-position:to bottom right}",
        "@supports (background-image:linear-gradient(in lab, red, red))",
        "{.bg-linear-to-br{--tw-gradient-position:to bottom right}}",
      ].join(""),
    );
  });

  it("removes the hint from a gradient written in a declaration value", async () => {
    // The arbitrary-value form: the gradient is spelled out, not a variable
    const css = ".a{background-image:linear-gradient(to bottom right in oklab,red,blue)}";

    expect((await compile(css)).trim()).toBe(".a{background-image:linear-gradient(to bottom right,red,blue)}");
  });

  it("leaves the color space of color-mix() alone", async () => {
    // color-mix() has its own polyfill and needs the hint to be parsed, so the
    // gradient cleanup must not strip it
    const css = ".a{background:color-mix(in oklab, var(--x) 20%, transparent)}";

    expect((await compile(css)).trim()).toBe(".a{background:rgb(var(--x-rgb) / 0.2)}");
  });

  it("keeps a position that is only a hint", async () => {
    // A radial or conic gradient without a position stores the hint alone.
    // Stripping it would leave an empty first argument in the gradient, which
    // is invalid as well, so it is kept as it is.
    const css = ".a{--tw-gradient-position:in oklab}";

    expect((await compile(css)).trim()).toBe(".a{--tw-gradient-position:in oklab}");
  });
});
