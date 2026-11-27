/**
 * Tests for the browser-compatibility PostCSS pipeline (postcss.config.ts).
 *
 * The pipeline lowers Tailwind v4 output to Chromium 108, the engine of the
 * packaged Electron. A regression here is silent: the build succeeds and the
 * stylesheet looks reasonable, but a declaration the engine cannot parse is
 * dropped at computed-value time and the element paints nothing. Gradients
 * broke exactly that way, so the hint removal is pinned here.
 */
import { readFileSync } from "node:fs";
import { relative } from "node:path";
import fg from "fast-glob";
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

/**
 * Collects the value of every inline `style` attribute of a component.
 *
 * Both the literal (`style="..."`) and the template (`style={`...`}`) forms
 * are read, because neither is compiled by PostCSS.
 *
 * Args:
 *     source (str): Component source
 *
 * Returns:
 *     list: Values of the style attributes
 */
function inlineStyles(source: string): string[] {
  const attributes = source.matchAll(/style=(?:"([^"]*)"|\{`([^`]*)`\})/g);
  return [...attributes].map((match) => match[1] ?? match[2] ?? "");
}

/**
 * Converts an Oklab color to 8-bit sRGB with the matrices of CSS Color 4.
 *
 * The expected value has to come from somewhere other than the polyfill under
 * test: a constant copied out of its output only pins whatever it happens to
 * emit. These matrices are the definition the polyfill implements, written out
 * here so the test checks the arithmetic instead of remembering a string.
 *
 * Args:
 *     lightness (number): Oklab lightness, 0 to 1
 *     a (number): Oklab a axis
 *     b (number): Oklab b axis
 *
 * Returns:
 *     list: sRGB channels in 0 to 255, before rounding. Only meaningful for a
 *         color inside the sRGB gamut.
 */
function oklabToSrgb(lightness: number, a: number, b: number): number[] {
  const long = lightness + 0.3963377774 * a + 0.2158037573 * b;
  const medium = lightness - 0.1055613458 * a - 0.0638541728 * b;
  const short = lightness - 0.0894841775 * a - 1.291485548 * b;
  const l = long ** 3;
  const m = medium ** 3;
  const s = short ** 3;
  const linear = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return linear.map((channel) => {
    const encoded = channel <= 0.0031308 ? 12.92 * channel : 1.055 * channel ** (1 / 2.4) - 0.055;
    return encoded * 255;
  });
}

/** CSS named colors used by the fixtures, as 8-bit sRGB channels plus alpha. */
const NAMED_COLORS: Record<string, number[]> = {
  red: [255, 0, 0, 1],
  blue: [0, 0, 255, 1],
  // css green is #008000: half of what "green" looks like on a monitor
  green: [0, 128, 0, 1],
  yellow: [255, 255, 0, 1],
  purple: [128, 0, 128, 1],
  white: [255, 255, 255, 1],
  orange: [255, 165, 0, 1],
  transparent: [0, 0, 0, 0],
};

/**
 * Mixes two colors the way `color-mix(in srgb, ...)` does.
 *
 * Weights are normalized and interpolated in premultiplied sRGB, so a fully
 * transparent color contributes no color while still carrying its weight: an
 * orange mixed with 30% transparent stays orange and loses 30% alpha.
 *
 * Args:
 *     first (str): Name of the first color
 *     firstWeight (number): Weight of the first color, 0 to 1
 *     second (str): Name of the second color
 *     secondWeight (number): Weight of the second color, 0 to 1
 *
 * Returns:
 *     list: sRGB channels in 0 to 255 and alpha, before rounding
 */
function mixSrgb(first: string, firstWeight: number, second: string, secondWeight: number): number[] {
  const [r1, g1, b1, a1] = NAMED_COLORS[first];
  const [r2, g2, b2, a2] = NAMED_COLORS[second];
  const alpha = a1 * firstWeight + a2 * secondWeight;
  const channel = (one: number, two: number) =>
    alpha === 0 ? 0 : (one * a1 * firstWeight + two * a2 * secondWeight) / alpha;
  return [channel(r1, r2), channel(g1, g2), channel(b1, b2), alpha];
}

/**
 * Reads the channels of the first rgb() or rgba() color of a declaration.
 *
 * Args:
 *     value (str): Declaration value, e.g. `.a{background:rgb(1, 2, 3)}`
 *
 * Returns:
 *     list: Red, green, blue in 0 to 255 and alpha in 0 to 1
 */
function parseColor(value: string): number[] {
  const match = value.match(/rgba?\(([^)]*)\)/);
  if (!match) throw new Error(`no rgb() color in ${JSON.stringify(value)}`);
  const channels = match[1]
    .split(/[,\s/]+/)
    .filter(Boolean)
    .map((part) => Number.parseFloat(part));
  if (channels.length < 3 || channels.some(Number.isNaN)) {
    throw new Error(`unreadable rgb() color in ${JSON.stringify(value)}`);
  }
  return [channels[0], channels[1], channels[2], channels[3] ?? 1];
}

/**
 * Tolerance of a converted sRGB channel, in 8-bit steps.
 *
 * Every implementation rounds to whole bytes at the end, so a channel may land
 * one step away from the unrounded reference.
 */
const CHANNEL_TOLERANCE = 1;

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

describe("TestOklabConversion", () => {
  // Chromium 108 (Electron 22) can not parse oklab(): it drops the declaration
  // and the element paints nothing, so the polyfill rewrites every static
  // oklab() into rgb(). What it must not get wrong is the color itself, and an
  // rgb() value is only obviously right or wrong to an eye. These cases are
  // inside the sRGB gamut, where the color spec leaves no freedom: any correct
  // implementation lands on the same bytes.
  it.each([
    { css: "oklab(0.65 0.15 0.1)", lightness: 0.65, a: 0.15, b: 0.1 },
    { css: "oklab(0.7 0.2 -0.15)", lightness: 0.7, a: 0.2, b: -0.15 },
    { css: "oklab(0.6 0.05 0.08)", lightness: 0.6, a: 0.05, b: 0.08 },
    { css: "oklab(0.5 0.02 -0.02)", lightness: 0.5, a: 0.02, b: -0.02 },
  ])("converts $css with the matrices of the color spec", async ({ css, lightness, a, b }) => {
    const compiled = (await compile(`.a{background:${css}}`)).trim();
    const expected = oklabToSrgb(lightness, a, b);
    // The whole case rests on the fixture being in gamut: outside of it the
    // answer depends on the gamut mapping algorithm (see the test below).
    expect(expected.every((channel) => channel >= 0 && channel <= 255)).toBe(true);

    const actual = parseColor(compiled);
    expected.forEach((channel, index) => {
      expect(Math.abs(actual[index] - channel)).toBeLessThanOrEqual(CHANNEL_TOLERANCE);
    });
    expect(actual[3]).toBe(1);
  });

  it("declares a wide-gamut color after its sRGB fallback", async () => {
    // oklab(0.5 -0.1 0.15) is outside sRGB, so there is no single correct rgb()
    // value: the spec asks for a gamut mapping and the exact bytes depend on
    // the algorithm. The polyfill emits a mapped rgb() for engines that can
    // only do sRGB and a display-p3 value for those that can do more. The order
    // is the contract: Chromium 108 rejects the p3 line and keeps the rgb()
    // already declared, a newer engine takes the p3 line.
    const compiled = (await compile(".a{background:oklab(0.5 -0.1 0.15)}")).trim();
    const declared = [...compiled.matchAll(/background:([^;}]+)/g)].map((match) => match[1].trim());

    expect(declared).toHaveLength(2);
    expect(declared[0]).toMatch(/^rgba?\(/);
    expect(declared[1]).toMatch(/^color\(display-p3 /);
    // the fallback still has to be a printable color
    expect(
      parseColor(declared[0])
        .slice(0, 3)
        .every((channel) => channel >= 0 && channel <= 255),
    ).toBe(true);
  });
});

describe("TestColorMixConversion", () => {
  // The same reasoning as for oklab(): color-mix() is unsupported in Chromium
  // 108, Tailwind's opacity modifiers are rewritten by the project plugins, and
  // a mix written by hand in a stylesheet is lowered to a plain rgb() value.
  // The expected color is computed from the definition of the mix, not read
  // back from the plugin.
  it.each([
    { css: "color-mix(in srgb, red, blue)", first: "red", firstWeight: 0.5, second: "blue", secondWeight: 0.5 },
    {
      css: "color-mix(in srgb, green 70%, yellow)",
      first: "green",
      firstWeight: 0.7,
      second: "yellow",
      secondWeight: 0.3,
    },
    {
      css: "color-mix(in srgb, purple, white 50%)",
      first: "purple",
      firstWeight: 0.5,
      second: "white",
      secondWeight: 0.5,
    },
    {
      css: "color-mix(in srgb, orange, transparent 30%)",
      first: "orange",
      firstWeight: 0.7,
      second: "transparent",
      secondWeight: 0.3,
    },
  ])("mixes $css with the weights of the color spec", async ({ css, first, firstWeight, second, secondWeight }) => {
    const compiled = (await compile(`.a{background:${css}}`)).trim();
    const expected = mixSrgb(first, firstWeight, second, secondWeight);
    const actual = parseColor(compiled);

    expected.forEach((value, index) => {
      const tolerance = index === 3 ? 0.01 : CHANNEL_TOLERANCE;
      expect(Math.abs(actual[index] - value)).toBeLessThanOrEqual(tolerance);
    });
  });
});

describe("TestInlineStyleAttributes", () => {
  it("keeps color functions that need lowering in stylesheets", () => {
    // PostCSS only compiles stylesheets. A declaration written in a `style`
    // attribute reaches the engine verbatim, Chromium 108 drops it, and the
    // element renders as if no color had been set at all.
    const root = process.cwd();
    const files = fg.sync("src/**/*.svelte", { cwd: root, absolute: true });
    const offenders = files.flatMap((file) =>
      inlineStyles(readFileSync(file, "utf-8"))
        .filter((value) => /(?:oklab|oklch|color-mix)\(/.test(value))
        .map((value) => `${relative(root, file)}: style="${value}"`),
    );

    expect(offenders).toEqual([]);
  });
});
