/**
 * Tests for the browser-compatibility PostCSS pipeline of the electron app
 * (postcss.config.mts), which styles the renderer.
 *
 * The pipeline is a copy of `frontend/postcss.config.ts`: the two packages are
 * installed and built independently (each has its own pnpm-lock), so they can
 * not share a module. Only the import order differs, and the last test here
 * keeps the copies from drifting apart.
 *
 * A regression in this pipeline is silent: the build succeeds and the
 * stylesheet looks reasonable, but a declaration the engine cannot parse is
 * dropped at computed-value time and the element paints nothing. Gradients
 * broke exactly that way on Chromium 108 (the engine of Electron 22), so the
 * hint removal is pinned here.
 */
import postcss from "postcss";
import { describe, expect, it } from "vitest";
import config from "../postcss.config.mts";

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
    ["to bottom in oklab", "to bottom"],
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

  it("keeps the color space of color-mix() alone", async () => {
    // color-mix() has its own polyfill and needs the hint to be parsed
    const css = ".a{background:color-mix(in oklab, var(--x) 20%, transparent)}";

    expect((await compile(css)).trim()).toBe(".a{background:rgb(var(--x-rgb) / 0.2)}");
  });
});

describe("TestPipelineCopies", () => {
  it("matches the frontend copy of the pipeline", async () => {
    // The two configs must stay the same pipeline: a fix applied to one and not
    // the other ships a different appearance to the packaged app than to the
    // browser build. Only the import order is allowed to differ.
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    // `pnpm test` runs with the package root as the working directory
    const root = process.cwd();
    const normalize = (path: string) =>
      readFileSync(path, "utf8")
        .split("\n")
        .filter((line) => !line.startsWith("import ") && !line.startsWith("// @ts-ignore"))
        .map((line) => line.trimEnd())
        .join("\n")
        .trim();

    expect(normalize(join(root, "postcss.config.mts"))).toBe(
      normalize(join(root, "..", "frontend", "postcss.config.ts")),
    );
  });
});
