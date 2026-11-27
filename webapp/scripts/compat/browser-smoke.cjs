"use strict";
// Browser smoke test for the browser-compatibility page, in the real engine.
//
// The postcss pipeline lowers what Tailwind v4 emits so Chromium 108 (electron
// 22, the engine the client ships) can paint the page: `oklab()` and
// `color-mix()` become `rgb()`, nested rules become flat selectors, media
// ranges become `min-width`, `translate`/`scale`/`rotate` get a `transform`
// fallback, and the color-space hint is stripped from gradients. None of that
// is proven by reading the stylesheet: a declaration the engine can not parse
// is dropped silently, and a declaration it parses but can not compute at
// computed-value time leaves the element unpainted. Both look the same on
// screen — a blank cell — which is what a person used to check by eye.
//
// This script loads the built stylesheets in electron and renders one probe per
// compatibility feature. Every probe is compared against something the engine
// itself produced, never against a picture checked into the repository:
//
//   colors        the swatch must compute to the color its own rule declares,
//                 and the screenshot must show that color
//   gradients     the image must survive, and the two ends must differ
//   opacity       `bg-x/50` must have the channels of `bg-x` and half its
//                 alpha; the screenshot must show them composited on white
//   transforms    `translate-x-4` and friends must land in the same box as an
//                 explicit `transform: translate(16px, 8px)` declaration, and
//                 rasterize to the same pixels
//   nesting       a child painted by a rule written with `&` must look exactly
//                 like a child painted by the flat rule with the same color
//   variants      `dark:bg-x` inside a `.dark` ancestor must equal `bg-x`, and
//                 must paint nothing without that ancestor
//   hover         `hover:bg-x` must apply only while the pointer is over the
//                 element; `hover:scale-110` must grow the box by 1.1
//   breakpoints   `md:bg-x`/`lg:bg-x` must switch at the width the media query
//                 names, measured by resizing the window
//
// The control phase loads raw `oklab()`, `color-mix()` and nested CSS, which the
// engine must not be able to use. Without it a green run would not distinguish
// "the polyfill made the page paintable" from "this engine understands the
// syntax natively", and the check would be evidence of nothing.
//
// The compat page itself is Svelte markup and is not loaded here: its styles
// are what the pipeline emits, and a probe with the same class list goes
// through exactly the same cascade, without a dev server or an authenticated
// app shell in CI.
//
// Requires a prior `vite build` of the frontend:
//   cd frontend; pnpm run smoke:compat
//   electron scripts/compat/browser-smoke.cjs --css path/to/build [--css ...]
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const webappRoot = path.resolve(__dirname, "..", "..");
const repoRoot = path.resolve(webappRoot, "..");
const frontendBuild = path.join(repoRoot, "frontend", "build");

// The engine has to mount a page and read a computed style, both of which are
// immediate; the timeout only bounds a hung renderer.
const PHASE_TIMEOUT_MS = 30000;

// One window per phase. The probes of a phase are packed into as few rows as
// they fit, so the height only has to cover the tallest phase; the width has to
// cover the packed rows plus the narrowest breakpoint of the media phase.
const WINDOW_WIDTH = 1000;
const WINDOW_HEIGHT = 620;

// Probe geometry, in CSS pixels. Every case gets a stage on a grid and every
// slot inside it sits at a fixed offset, so the screenshot has a known pixel
// for each measured element whatever the stylesheet does to layout.
const SLOT_SIZE = 64;
const SLOT_PITCH = 72;
const STAGE_HEIGHT = 160;
const CELL_GAP = 8;
const CELL_ORIGIN = 8;
const GEOMETRY = { size: SLOT_SIZE, pitch: SLOT_PITCH, stage: STAGE_HEIGHT, gap: CELL_GAP, origin: CELL_ORIGIN };

// The page paints on white; a translucent probe has to be composited over it
// before its color can be compared to a pixel.
const PAGE_BACKGROUND = 255;

// A solid probe rasterizes to one exact color with acceleration off and the
// sRGB profile forced. The tolerance covers the last bit of a channel that a
// different rasterizer may round the other way, nothing more: it must not be
// wide enough to accept a wrong color.
const PIXEL_TOLERANCE = 2;

// Two probes must land in the same box. Layout rounds to 1/64 px, so a
// difference below this is not a difference in the transform.
const GEOMETRY_TOLERANCE = 0.6;

// Raw declarations the engine under test must not be able to use. `green`,
// `blue` and `rgb()` are ordinary values, so only the modern syntax can be the
// reason a declaration is dropped.
const CONTROL_CSS = [
  ".control-oklab { background: oklab(0.65 0.15 0.1); }",
  ".control-color-mix { background: color-mix(in srgb, green, blue); }",
  ".control-nest { background: rgb(1, 2, 3); & .control-nest-child { background: rgb(4, 5, 6); } }",
].join("\n");

/**
 * Parse `--name value` and `--name=value` arguments.
 *
 * @param {string[]} argv Arguments after the script path
 * @returns {Record<string, string[]>} Values by name, in argument order
 */
function parseArgs(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith("--")) continue;
    const separator = arg.indexOf("=");
    const name = separator === -1 ? arg.slice(2) : arg.slice(2, separator);
    const value = separator === -1 ? argv[(index += 1)] : arg.slice(separator + 1);
    if (value === undefined) break;
    (out[name] ??= []).push(value);
  }
  return out;
}

/**
 * Collect the stylesheets to test.
 *
 * A directory is walked recursively for `*.css`: the build splits the styles by
 * route, so the rules of one page are not guaranteed to live in one file.
 *
 * @param {string} source File or directory given on the command line
 * @returns {{ file: string, css: string }[]} Stylesheets in a stable order
 */
function collectSheets(source) {
  let stats;
  try {
    stats = fs.statSync(source);
  } catch (error) {
    throw new Error(`can not read ${source}: ${error.message}`);
  }

  if (stats.isFile()) return [{ file: source, css: fs.readFileSync(source, "utf8") }];

  const files = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".css")) files.push(full);
    }
  };
  walk(source);
  files.sort();
  if (files.length === 0) throw new Error(`no stylesheet found under ${source}, run the build first`);
  return files.map((file) => ({ file: path.relative(source, file), css: fs.readFileSync(file, "utf8") }));
}

/**
 * The probe evaluated in the page: build one stage per case, one element per
 * slot, and read what the engine made of the class list.
 *
 * Strings and numbers only cross the boundary, so this function is serialized
 * with `toString()` and must not refer to anything outside its own body.
 *
 * Cases have the shape
 *   { id, slots: [{ name, classes, parentClasses?, style?, token? }] }
 * A slot element is placed at a fixed offset inside its stage; `offset` is its
 * box relative to that placement, which is where a transform shows up. Two
 * slots of one case are compared to each other, so the stage origin cancels.
 *
 * The declared value is read back from the document's own stylesheets: the
 * expectation then comes from the stylesheet rather than from a copy of it in
 * this script. The last matching rule wins, specificity is not modelled — the
 * probe classes are single utilities, so the only competition is shadowing.
 *
 * Args:
 *     cases (object[]): Cases to render
 *
 * Returns:
 *     object: {records, viewport}
 *         records: one per slot
 *         viewport: {width, height} of the page in CSS pixels
 */
function renderProbes(cases, geometry) {
  const size = geometry.size;
  const pitch = geometry.pitch;
  const stageHeight = geometry.stage;
  const gap = geometry.gap;
  const origin = geometry.origin;
  const limit = Math.max(320, window.innerWidth - 16);

  // Svelte scopes a component's `<style>` rules with a `svelte-*` class that the
  // page's own markup carries. A probe built here has to carry it too, or the
  // page-defined rules (the swatches, the nested rule) never match.
  const lookup = (token) => {
    const found = { scope: [], backgrounds: [], declaredColor: "", backgroundImage: "" };
    if (!token) return found;
    // A selector escapes the characters a class name may not contain (`.md\:bg-x`,
    // `.bg-x\/50`); the token arrives unescaped, so the selector is unescaped
    // before matching instead of escaping the token.
    const pattern = new RegExp("\\." + token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\w-])");
    const visit = (rules) => {
      for (const rule of rules) {
        if (rule.cssRules) visit(rule.cssRules);
        if (!rule.selectorText || !rule.style) continue;
        const selector = rule.selectorText.split("\\").join("");
        if (!pattern.test(selector)) continue;
        for (const match of selector.matchAll(/\.([\w-]+)/g)) {
          if (match[1].startsWith("svelte-") && !found.scope.includes(match[1])) found.scope.push(match[1]);
        }
        // A color outside sRGB is emitted twice, `rgb()` first and the wider
        // gamut second; CSSOM keeps only the last, so the declarations are read
        // in order from the rule text and the caller picks the first one it can
        // parse. On this engine that is the `rgb()` the pipeline wrote first.
        for (const match of rule.cssText.matchAll(/(?:^|[;{\s])background(?:-color)?\s*:\s*([^;}]+)/g)) {
          found.backgrounds.push(match[1].trim());
        }
        const image = rule.style.getPropertyValue("background-image");
        if (image) found.backgroundImage = image;
        // A shorthand that contains `var()` can not be expanded before it is
        // computed, so the longhand is empty for it; the shorthand is the
        // declaration in that case.
        const color = rule.style.getPropertyValue("background-color") || rule.style.getPropertyValue("background");
        if (color) found.declaredColor = color;
      }
    };
    for (const sheet of document.styleSheets) {
      try {
        visit(sheet.cssRules);
      } catch (error) {
        // A sheet the page can not read leaves the probe without a declared
        // value, which every probe that needs one reports as a failure.
      }
    }
    return found;
  };

  const records = [];
  let cursorX = origin;
  let cursorY = origin;
  let rowHeight = 0;

  for (const item of cases) {
    const width = 12 + item.slots.length * pitch;
    if (cursorX > origin && cursorX + width > limit) {
      cursorX = origin;
      cursorY += rowHeight + gap;
      rowHeight = 0;
    }
    const stage = document.createElement("div");
    stage.style.cssText =
      "position:absolute;margin:0;left:" +
      cursorX +
      "px;top:" +
      cursorY +
      "px;width:" +
      width +
      "px;height:" +
      stageHeight +
      "px";
    document.body.append(stage);
    cursorX += width + gap;
    rowHeight = Math.max(rowHeight, stageHeight);

    item.slots.forEach((slot, index) => {
      const holder = document.createElement("div");
      holder.style.cssText =
        "position:absolute;left:" + (12 + index * pitch) + "px;top:12px;width:" + size + "px;height:" + size + "px";
      stage.append(holder);

      const token = slot.from || slot.token || (slot.classes || "").split(/\s+/)[0] || "";
      const found = lookup(token);
      // A reference paints the declaration of another rule unconditionally: it
      // is the expected value for a rule that only applies in a state
      // (`:hover`, `.dark`, a breakpoint), read from the stylesheet instead of
      // copied into this script. With `fallback` it paints what the declaration
      // falls back to when its variable is undefined.
      const declared = slot.from ? found.declaredColor : "";
      const fallback = /^var\(\s*--[\w-]+\s*,\s*(.+?)\s*\)$/.exec(declared);
      const reference = declared ? "background:" + (slot.fallback && fallback ? fallback[1] : declared) : "";

      // The context is where a parent class lives (`.dark`, `.nest-probe`).
      // It is a full-size box so the slot's own placement stays comparable.
      const parentToken = (slot.parentClasses || "").split(/\s+/)[0] || "";
      const parentScope = lookup(parentToken).scope;
      const context = document.createElement("div");
      context.style.cssText = "position:absolute;left:0;top:0;width:100%;height:100%";
      context.className = [slot.parentClasses || "", ...parentScope].join(" ").trim();
      holder.append(context);

      const element = document.createElement("div");
      const slotName = slot.name || "subject";
      element.dataset.probe = item.id + ":" + slotName;
      element.className = [slot.classes || "", ...found.scope].join(" ").trim();
      element.style.cssText = [
        "position:absolute",
        "left:0",
        "top:0",
        "margin:0",
        "box-sizing:border-box",
        "width:" + size + "px",
        "height:" + size + "px",
        slot.style || "",
        reference,
      ].join(";");
      context.append(element);

      const computed = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      const anchor = holder.getBoundingClientRect();
      records.push({
        case: item.id,
        slot: slotName,
        classes: slot.classes || "",
        // Absolute box for the screenshot and for input events.
        box: { x: box.x, y: box.y, width: box.width, height: box.height },
        // Box relative to the slot placement: the transform, nothing else.
        offset: { x: box.x - anchor.x, y: box.y - anchor.y },
        color: computed.backgroundColor,
        image: computed.backgroundImage === "none" ? "" : computed.backgroundImage,
        text: computed.color,
        border: computed.borderColor,
        declared: {
          backgrounds: found.backgrounds,
          backgroundImage: found.backgroundImage,
        },
      });
    });
  }
  return { records, viewport: { width: window.innerWidth, height: window.innerHeight } };
}

/**
 * Wait for the renderer to paint what was just inserted.
 *
 * @param {BrowserWindow} win Window holding the phase page
 * @param {number} count Frames to wait for
 * @returns {Promise<void>} Resolves after the frames were produced
 */
function nextFrames(win, count = 2) {
  return win.webContents.executeJavaScript(
    "new Promise((resolve) => {" +
      "let left = " +
      count +
      ";" +
      "const step = () => (--left <= 0 ? resolve(true) : requestAnimationFrame(step));" +
      "requestAnimationFrame(step);" +
      "})",
    true,
  );
}

/**
 * Reads one pixel of a rasterized page.
 *
 * `nativeImage.toBitmap()` hands back the raw skia bitmap: 32-bit BGRA with
 * premultiplied alpha, four bytes per pixel. The page is opaque, so the alpha
 * channel is 255 and premultiplication changes nothing.
 *
 * Args:
 *     bitmap (Buffer): Raw bitmap data
 *     width (number): Image width in device pixels
 *     height (number): Image height in device pixels
 *     x (number): Column, in device pixels
 *     y (number): Row, in device pixels
 *
 * Returns:
 *     object | null: {r, g, b, a}, or null outside the image
 */
function readPixel(bitmap, width, height, x, y) {
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  const offset = (y * width + x) * 4;
  return { b: bitmap[offset], g: bitmap[offset + 1], r: bitmap[offset + 2], a: bitmap[offset + 3] };
}

/**
 * Screenshot the phase and attach the sampled pixels of every slot.
 *
 * @param {BrowserWindow} win Window holding the phase page
 * @param {object} result Records and viewport returned by the probe
 * @param {Function} pointsFor Record to sample points, as fractions of its box
 * @returns {Promise<object>} The probe result with pixels on every record
 */
async function samplePixels(win, result, pointsFor) {
  const image = await win.webContents.capturePage();
  const { width, height } = image.getSize();
  if (width === 0 || height === 0) throw new Error("the screenshot is empty, the window never rendered");
  // The screenshot is in device pixels, the records in CSS pixels; they only
  // differ when the display is scaled, which the run pins to 1 but this keeps
  // the sampling correct if that pin is ever lost.
  const scale = result.viewport.width > 0 ? width / result.viewport.width : 1;
  const bitmap = image.toBitmap();
  const read = (x, y) => readPixel(bitmap, width, height, Math.round(x * scale), Math.round(y * scale));

  for (const record of result.records) {
    record.pixels = pointsFor(record).map(([fractionX, fractionY]) =>
      read(record.box.x + record.box.width * fractionX, record.box.y + record.box.height * fractionY),
    );
    record.pixel = record.pixels[0] ?? null;
  }
  return { ...result, image: { width, height, scale } };
}

/**
 * Parse the channels of a CSS color as the engine writes it.
 *
 * Handles the forms the build produces: `rgb()`/`rgba()` with commas or spaces,
 * and the hex form of a converted theme variable.
 *
 * Args:
 *     value (str): Color, e.g. `rgb(232, 91, 62)` or `#8e51ff`
 *
 * Returns:
 *     number[] | null: [r, g, b, a] with channels in 0-255, null when unreadable
 */
function parseColor(value) {
  const text = (value ?? "").trim();
  const hex = /^#([0-9a-f]{3,8})$/i.exec(text);
  if (hex) {
    const digits = hex[1];
    if (digits.length === 3 || digits.length === 4) {
      const channels = digits.split("").map((digit) => Number.parseInt(digit + digit, 16));
      return [channels[0], channels[1], channels[2], channels.length === 4 ? channels[3] / 255 : 1];
    }
    if (digits.length === 6 || digits.length === 8) {
      const channels = digits.match(/.{2}/g).map((pair) => Number.parseInt(pair, 16));
      return [channels[0], channels[1], channels[2], channels.length === 4 ? channels[3] / 255 : 1];
    }
    return null;
  }
  const match = /^rgba?\(([^)]*)\)$/i.exec(text);
  if (!match) return null;
  const channels = match[1]
    .split(/[,\s/]+/)
    .filter(Boolean)
    .map((part) => Number.parseFloat(part));
  if (channels.length < 3 || channels.some(Number.isNaN)) return null;
  return [channels[0], channels[1], channels[2], channels[3] ?? 1];
}

/**
 * True when two colors agree on every channel within a tolerance.
 *
 * @param {number[]} left Channels in 0-255, alpha 0-1
 * @param {number[]} right Channels in 0-255, alpha 0-1
 * @param {number} tolerance Largest allowed difference per channel
 * @returns {boolean} Whether the colors are the same
 */
function sameColor(left, right, tolerance = 0.6) {
  return left.every((channel, index) => Math.abs(channel - right[index]) <= (index === 3 ? 0.01 : tolerance));
}

/**
 * True when a computed color paints nothing at all.
 *
 * @param {string} value Computed color
 * @returns {boolean} Whether the color is fully transparent
 */
function isTransparent(value) {
  const channels = parseColor(value);
  return channels !== null && channels[3] === 0;
}

/**
 * The color a probe shows once the page composites it.
 *
 * @param {number[]} channels Declared or computed channels, alpha included
 * @returns {number[]} Red, green, blue in 0-255, alpha dropped
 */
function overPage(channels) {
  const [r, g, b, a] = channels;
  const blend = (channel) => channel * a + PAGE_BACKGROUND * (1 - a);
  return [blend(r), blend(g), blend(b)];
}

/**
 * The largest per-channel distance between a pixel and an expected color.
 *
 * Args:
 *     pixel (object): Sampled {r, g, b}
 *     expected (number[]): Expected channels in 0-255
 *
 * Returns:
 *     number: Largest absolute difference, in 8-bit steps
 */
function pixelDistance(pixel, expected) {
  return Math.max(Math.abs(pixel.r - expected[0]), Math.abs(pixel.g - expected[1]), Math.abs(pixel.b - expected[2]));
}

/**
 * Renders channels for a message.
 *
 * Args:
 *     channels (number[]): Channels in 0-255
 *
 * Returns:
 *     str: e.g. `rgb(232, 91, 62)`
 */
function formatChannels(channels) {
  return (
    "rgb(" +
    channels
      .slice(0, 3)
      .map((value) => Math.round(value))
      .join(", ") +
    ")"
  );
}

/**
 * Renders a sampled pixel for a message.
 *
 * @param {object} pixel Sampled {r, g, b}
 * @returns {string} e.g. `rgb(232, 91, 62)`
 */
function formatPixel(pixel) {
  return formatChannels([pixel.r, pixel.g, pixel.b]);
}

/**
 * Names a record in a failure message.
 *
 * @param {object} record Record returned by the probe
 * @returns {string} e.g. `opacity-primary-50: bg-primary/50`
 */
function label(record) {
  return `${record.case}: ${record.classes || record.slot}`;
}

/**
 * Renders channels for every point sampled on a record.
 *
 * @param {object} record Record with sampled pixels
 * @returns {string} e.g. `rgb(1, 2, 3) rgb(4, 5, 6)`
 */
function formatPixels(record) {
  return (record.pixels ?? []).filter(Boolean).map(formatPixel).join(" ");
}

/* ------------------------------------------------------------------ cases */

// The four sample points around the center are where a rotation or a scale
// changes the picture while the center stays the same color.
const SHAPE_POINTS = [
  [0.5, 0.5],
  [0.25, 0.25],
  [0.75, 0.25],
  [0.25, 0.75],
  [0.75, 0.75],
];

const CORNERS = () => SHAPE_POINTS;
const CENTER = () => [[0.5, 0.5]];
const GRADIENT_POINTS = () => [
  [0.05, 0.5],
  [0.95, 0.5],
  [0.5, 0.5],
];

// Colors: the swatch is compared to the color its own rule declares.
const COLOR_CASES = [
  { id: "oklab-1", slots: [{ classes: "swatch-oklab-1" }] },
  { id: "oklab-2", slots: [{ classes: "swatch-oklab-2" }] },
  { id: "oklab-3", slots: [{ classes: "swatch-oklab-3" }] },
  { id: "oklab-4", slots: [{ classes: "swatch-oklab-4" }] },
  { id: "mix-1", slots: [{ classes: "swatch-mix-1" }] },
  { id: "mix-2", slots: [{ classes: "swatch-mix-2" }] },
  { id: "mix-3", slots: [{ classes: "swatch-mix-3" }] },
  { id: "mix-4", slots: [{ classes: "swatch-mix-4" }] },
].map((item) => ({ ...item, kind: "color" }));

const GRADIENT_CASES = [
  { id: "gradient-mix", slots: [{ classes: "swatch-gradient-mix" }] },
  { id: "gradient-oklab", slots: [{ classes: "swatch-gradient-oklab" }] },
  { id: "gradient-utilities", slots: [{ classes: "bg-linear-to-r from-blue-500 via-purple-500 to-pink-500" }] },
].map((item) => ({ ...item, kind: "gradient" }));

// Body of the compatibility page, section 7: every swatch is a `bg-x/NN`
// utility, the reference is the same color at full opacity.
const OPACITY_CASES = [
  ["primary-75", "bg-primary/75", "bg-primary", 0.75],
  ["primary-50", "bg-primary/50", "bg-primary", 0.5],
  ["primary-20", "bg-primary/20", "bg-primary", 0.2],
  ["destructive-75", "bg-destructive/75", "bg-destructive", 0.75],
  ["foreground-20", "bg-foreground/20", "bg-foreground", 0.2],
  ["orange-75", "bg-orange-500/75", "bg-orange-500", 0.75],
  ["blue-50", "bg-blue-600/50", "bg-blue-600", 0.5],
  ["green-50", "bg-green-500/50", "bg-green-500", 0.5],
].map(([id, subject, reference, factor]) => ({
  id,
  property: "backgroundColor",
  factor,
  slots: [
    { name: "subject", classes: subject },
    { name: "reference", classes: reference },
  ],
}));

// Text and border colors carry the same polyfill but no large area to sample,
// so they are compared as computed values only.
const OPACITY_DECORATION_CASES = [
  ["text-primary-50", "text-primary/50", "text-primary", 0.5],
  ["border-border-50", "border-4 border-border/50", "border-4 border-border", 0.5],
  ["border-primary-50", "border-4 border-primary/50", "border-4 border-primary", 0.5],
].map(([id, subject, reference, factor]) => ({
  id,
  property: id.startsWith("text") ? "color" : "borderColor",
  factor,
  slots: [
    { name: "subject", classes: subject },
    { name: "reference", classes: reference },
  ],
}));

// Section 1: every shortcut against the transform it is short for. The oracle
// is written in the oldest form of the property, which any engine paints.
const TRANSFORM_CASES = [
  ["translate", "translate-x-4 translate-y-2", "transform:translate(16px, 8px)"],
  ["scale", "scale-125", "transform:scale(1.25)"],
  ["rotate", "rotate-12", "transform:rotate(12deg)"],
  ["combined", "translate-x-8 scale-110 rotate-6", "transform:translate(32px, 0) rotate(6deg) scale(1.1)"],
].map(([id, classes, style]) => ({
  id,
  slots: [
    { name: "subject", classes: `rounded-lg bg-linear-to-br from-blue-500 to-purple-600 ${classes}` },
    { name: "oracle", classes: "rounded-lg bg-linear-to-br from-blue-500 to-purple-600", style },
  ],
}));

// Section 5: the same color written once with `&` and once flat.
const NESTING_CASES = [
  {
    id: "nested-rule",
    slots: [
      { name: "subject", parentClasses: "nest-probe", classes: "nest-probe-child" },
      { name: "reference", classes: "nest-oracle-child" },
    ],
  },
];

// Section 7: an undefined variable must fall back to the declared color.
const FALLBACK_CASES = [
  {
    id: "undefined-variable",
    slots: [
      { name: "subject", classes: "var-fallback-probe" },
      { name: "reference", from: "var-fallback-probe", fallback: true },
    ],
  },
];

// Section 5: the `dark:` variant is a nested selector, and it must not apply
// outside a `.dark` ancestor.
const VARIANT_CASES = [
  {
    id: "dark-variant",
    slots: [
      { name: "subject", parentClasses: "dark", classes: "dark:bg-gray-800" },
      { name: "reference", from: "dark:bg-gray-800" },
      { name: "outside", classes: "dark:bg-gray-800" },
    ],
  },
];

// Section 5 hover card and section 9 transform-on-hover.
const HOVER_CASES = [
  {
    id: "hover-color",
    kind: "color",
    slots: [
      { name: "subject", classes: "bg-blue-100 hover:bg-blue-200" },
      { name: "base", from: "bg-blue-100" },
      { name: "active", from: "hover:bg-blue-200" },
    ],
  },
  { id: "hover-scale", kind: "scale", factor: 1.1, slots: [{ name: "subject", classes: "hover:scale-110" }] },
];

// Section 6: the responsive box and one reference per breakpoint.
const BREAKPOINT_CASES = [
  {
    id: "breakpoints",
    slots: [
      { name: "subject", classes: "bg-red-100 md:bg-yellow-100 lg:bg-green-100" },
      { name: "base", from: "bg-red-100" },
      { name: "md", from: "md:bg-yellow-100" },
      { name: "lg", from: "lg:bg-green-100" },
    ],
  },
];

/* --------------------------------------------------------------- phases */

/**
 * Write the page for a phase and load it in a window.
 *
 * The stylesheets are copied next to the page so a relative `url()` of a font
 * or an image keeps pointing at a missing file instead of at a different one;
 * only geometry and backgrounds are read here, so a missing asset changes
 * nothing.
 *
 * @param {string} workDir Scratch directory of this run
 * @param {string} name Phase name, also the directory name
 * @param {{ file: string, css: string }[]} sheets Stylesheets to load
 * @returns {Promise<BrowserWindow>} Window with the phase page loaded
 */
async function openPhase(workDir, name, sheets) {
  const phaseDir = path.join(workDir, name);
  fs.mkdirSync(phaseDir, { recursive: true });

  const links = sheets.map((sheet, index) => {
    const file = `sheet-${index}.css`;
    fs.writeFileSync(path.join(phaseDir, file), sheet.css);
    return `<link rel="stylesheet" href="${file}">`;
  });
  const page = path.join(phaseDir, "index.html");
  fs.writeFileSync(
    page,
    `<!doctype html><html><head><meta charset="utf-8"><title>${name}</title>` +
      `<style>html,body{margin:0;padding:0;background:#fff}</style>` +
      `${links.join("")}</head><body></body></html>`,
  );

  const win = new BrowserWindow({
    show: false,
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });

  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`the phase did not load within ${PHASE_TIMEOUT_MS} ms`)),
      PHASE_TIMEOUT_MS,
    );
    win.webContents.once("did-fail-load", (_event, code, description) => {
      clearTimeout(timer);
      reject(new Error(`the page failed to load: ${description} (${code})`));
    });
    win.webContents.once("did-finish-load", () => {
      clearTimeout(timer);
      resolve();
    });
    win.loadFile(page).catch((error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
  return win;
}

/**
 * Render the cases on a cleared page and screenshot them.
 *
 * @param {BrowserWindow} win Window holding the phase page
 * @param {object[]} cases Cases to render
 * @param {Function} pointsFor Record to sample points
 * @returns {Promise<object>} Probe result with pixels
 */
async function render(win, cases, pointsFor) {
  const result = await win.webContents.executeJavaScript(
    `(() => {
      document.body.replaceChildren();
      try {
        return (${renderProbes.toString()})(${JSON.stringify(cases)}, ${JSON.stringify(GEOMETRY)});
      } catch (error) {
        return { error: String((error && error.stack) || error) };
      }
    })()`,
    true,
  );
  if (result.error) throw new Error(`the probe failed in the page: ${result.error}`);
  await nextFrames(win, 2);
  return await samplePixels(win, result, pointsFor);
}

/**
 * Find the record of one slot of a case.
 *
 * @param {object[]} records Records returned by the probe
 * @param {string} caseId Case id
 * @param {string} slot Slot name
 * @returns {object} The record
 */
function slotOf(records, caseId, slot) {
  const record = records.find((candidate) => candidate.case === caseId && candidate.slot === slot);
  if (!record) throw new Error(`case ${caseId} has no slot ${slot}`);
  return record;
}

/**
 * Reads an element that is already on the page, without re-rendering it.
 *
 * The hover phase needs the same element before and after the pointer moved:
 * re-rendering would replace the node the pointer is over and lose the state
 * the phase is about.
 *
 * @param {BrowserWindow} win Window holding the phase page
 * @param {string} caseId Case id
 * @param {string} slot Slot name
 * @returns {Promise<object|null>} Color, image and box of the element
 */
async function readElement(win, caseId, slot) {
  return win.webContents.executeJavaScript(
    `(() => {
      const element = document.querySelector('[data-probe="${caseId}:${slot}"]');
      if (!element) return null;
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return {
        color: style.backgroundColor,
        image: style.backgroundImage === "none" ? "" : style.backgroundImage,
        text: style.color,
        box: { x: box.x, y: box.y, width: box.width, height: box.height },
      };
    })()`,
    true,
  );
}

/**
 * The first declared background color that parses.
 *
 * The pipeline emits a color twice when it is outside sRGB, the `rgb()`
 * fallback first and the wider gamut second. The engine under test can not use
 * the second one, so the first parseable declaration is what it paints and what
 * the probe has to expect.
 *
 * @param {object} record Record returned by the probe
 * @returns {{ value: string, channels: number[] } | null} The declaration
 */
function firstDeclaredColor(record) {
  for (const value of record.declared.backgrounds ?? []) {
    const channels = parseColor(value);
    if (channels) return { value, channels };
  }
  return null;
}

/**
 * Judge a swatch against the color its own rule declares.
 *
 * @param {object} record Record of the swatch
 * @returns {string[]} Problems, empty when the swatch is right
 */
function judgeDeclaredColor(record) {
  const declared = firstDeclaredColor(record);
  if (!declared) {
    return [
      `${label(record)}: no background-color is declared for it ` +
        `(${JSON.stringify(record.declared.backgrounds ?? [])}): the rule is not in the build`,
    ];
  }
  const computed = parseColor(record.color);
  if (!computed || computed[3] === 0) {
    return [`${label(record)}: nothing paints (computed color ${record.color})`];
  }
  if (!sameColor(declared.channels, computed)) {
    return [`${label(record)}: the engine computes ${record.color} instead of the declared ${declared.value}`];
  }
  const expected = overPage(computed);
  const distance = pixelDistance(record.pixel, expected);
  if (distance > PIXEL_TOLERANCE) {
    return [
      `${label(record)}: the screenshot shows ${formatPixel(record.pixel)} where ${formatChannels(expected)} ` +
        `is expected (off by ${distance}), declared ${declared.value}`,
    ];
  }
  return [];
}

/**
 * Judge a gradient: it must survive the engine, and its ends must differ.
 *
 * @param {object} record Record of the gradient
 * @returns {string[]} Problems, empty when the gradient paints
 */
function judgeGradient(record) {
  const problems = [];
  const declared = (record.declared.backgrounds ?? []).length > 0 || record.declared.backgroundImage;
  if (!declared) {
    problems.push(`${label(record)}: no background is declared for it: the rule is not in the build`);
  }
  if (!record.image) {
    problems.push(`${label(record)}: the engine computes no background image (the declaration was dropped)`);
    return problems;
  }
  const [left, right] = record.pixels;
  if (!left || !right) {
    problems.push(`${label(record)}: the gradient could not be sampled`);
    return problems;
  }
  if (left.r === right.r && left.g === right.g && left.b === right.b) {
    problems.push(`${label(record)}: the screenshot shows one color ${formatPixel(left)} across the gradient`);
  }
  return problems;
}

/**
 * Judge one opacity probe against its reference.
 *
 * The reference is the same color at full strength, painted by the engine
 * itself, so the expected channels never are a copy of the palette in this
 * script. A translucent color of a color that already has alpha (the theme
 * border) keeps that alpha multiplied in, which is what `calc(var(--x-a) * N)`
 * exists for.
 *
 * @param {object[]} records Records of the case
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when the pair is consistent
 */
function judgeOpacity(records, test) {
  const subject = slotOf(records, test.id, "subject");
  const reference = slotOf(records, test.id, "reference");
  const problems = [];
  const read = (record) => {
    if (test.property === "backgroundColor") return parseColor(record.color);
    if (test.property === "color") return parseColor(record.text);
    return parseColor(record.border);
  };

  const referenceColor = read(reference);
  const subjectColor = read(subject);
  if (!referenceColor || referenceColor[3] === 0) {
    problems.push(`${label(reference)}: the reference computes no ${test.property}`);
    return problems;
  }
  if (!subjectColor || subjectColor[3] === 0) {
    problems.push(`${label(subject)}: nothing paints (computed ${test.property} ${subject.color})`);
    return problems;
  }
  const [r, g, b, alpha] = referenceColor;
  const expected = [r, g, b, alpha * test.factor];
  if (!sameColor(expected, subjectColor)) {
    problems.push(
      `${label(subject)}: computes rgba(${subjectColor
        .slice(0, 3)
        .map((c) => Math.round(c))
        .join(", ")} / ` +
        `${subjectColor[3]}) where ${test.factor} of the reference is expected ` +
        `(rgba(${expected
          .slice(0, 3)
          .map((c) => Math.round(c))
          .join(", ")} / ${expected[3]}) from ` +
        `${reference.classes})`,
    );
    return problems;
  }
  if (test.property !== "backgroundColor") return problems;

  const composited = overPage(subjectColor);
  const distance = pixelDistance(subject.pixel, composited);
  if (distance > PIXEL_TOLERANCE) {
    problems.push(
      `${label(subject)}: the screenshot shows ${formatPixel(subject.pixel)} where ` +
        `${formatChannels(composited)} is expected (off by ${distance})`,
    );
  }
  return problems;
}

/**
 * Judge a transform probe against the explicit transform that stands for it.
 *
 * The box is the observable: two elements of the same size, one translate,
 * scale or rotate apart, land in different boxes, and a declaration the engine
 * dropped leaves the subject where the plain box was.
 *
 * @param {object[]} records Records of the case
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when both land in the same box
 */
function judgeTransform(records, test) {
  const subject = slotOf(records, test.id, "subject");
  const oracle = slotOf(records, test.id, "oracle");
  const problems = [];

  for (const axis of ["x", "y"]) {
    if (Math.abs(subject.offset[axis] - oracle.offset[axis]) > GEOMETRY_TOLERANCE) {
      problems.push(
        `${label(subject)}: the box starts at ${subject.offset[axis].toFixed(2)}px in ${axis} where ` +
          `${oracle.offset[axis].toFixed(2)}px is expected (the explicit transform of the oracle)`,
      );
    }
  }
  for (const side of ["width", "height"]) {
    if (Math.abs(subject.box[side] - oracle.box[side]) > GEOMETRY_TOLERANCE) {
      problems.push(
        `${label(subject)}: the box is ${subject.box[side].toFixed(2)}px ${side} where ` +
          `${oracle.box[side].toFixed(2)}px is expected`,
      );
    }
  }
  if (problems.length > 0) return problems;

  const differing = subject.pixels.findIndex((pixel, index) => {
    const other = oracle.pixels[index];
    return !other || pixelDistance(pixel, [other.r, other.g, other.b]) > PIXEL_TOLERANCE;
  });
  if (differing !== -1) {
    problems.push(
      `${label(subject)}: the screenshot shows ${formatPixel(subject.pixels[differing])} where the oracle ` +
        `shows ${formatPixel(oracle.pixels[differing])}`,
    );
  }
  return problems;
}

/**
 * Judge a nested rule against the flat rule of the same color.
 *
 * @param {object[]} records Records of the case
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when the nested child paints like the flat one
 */
function judgeNesting(records, test) {
  const subject = slotOf(records, test.id, "subject");
  const reference = slotOf(records, test.id, "reference");
  const subjectColor = parseColor(subject.color);
  const referenceColor = parseColor(reference.color);
  if (!referenceColor || referenceColor[3] === 0) {
    return [`${label(reference)}: the flat reference rule is missing from the build`];
  }
  if (!subjectColor || subjectColor[3] === 0) {
    return [
      `${label(subject)}: the child painted by the nested rule paints nothing ` +
        `(the engine dropped it, or the polyfill did not flatten it)`,
    ];
  }
  if (!sameColor(subjectColor, referenceColor)) {
    return [
      `${label(subject)}: the nested rule computes ${subject.color} where the flat rule ` +
        `${reference.classes} computes ${reference.color}`,
    ];
  }
  const distance = pixelDistance(subject.pixel, [reference.pixel.r, reference.pixel.g, reference.pixel.b]);
  if (distance > PIXEL_TOLERANCE) {
    return [
      `${label(subject)}: the screenshot shows ${formatPixel(subject.pixel)} where the flat rule shows ` +
        `${formatPixel(reference.pixel)}`,
    ];
  }
  return [];
}

/**
 * Judge the `dark:` variant with and without its ancestor.
 *
 * @param {object[]} records Records of the case
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when the variant matches the plain utility
 */
function judgeVariant(records, test) {
  const subject = slotOf(records, test.id, "subject");
  const reference = slotOf(records, test.id, "reference");
  const outside = slotOf(records, test.id, "outside");
  const subjectColor = parseColor(subject.color);
  const referenceColor = parseColor(reference.color);
  const problems = [];
  if (!referenceColor) {
    problems.push(`${label(reference)}: the plain utility is missing from the build`);
  } else if (!subjectColor || subjectColor[3] === 0) {
    problems.push(`${label(subject)}: inside .dark it paints nothing, the variant rule was lost`);
  } else if (!sameColor(subjectColor, referenceColor)) {
    problems.push(
      `${label(subject)}: inside .dark it computes ${subject.color} where ${reference.color} ` +
        `(${reference.classes}) is expected`,
    );
  } else {
    const distance = pixelDistance(subject.pixel, [reference.pixel.r, reference.pixel.g, reference.pixel.b]);
    if (distance > PIXEL_TOLERANCE) {
      problems.push(
        `${label(subject)}: the screenshot shows ${formatPixel(subject.pixel)} where ${formatPixel(reference.pixel)} ` +
          `is expected`,
      );
    }
  }
  if (!isTransparent(outside.color)) {
    problems.push(`${label(outside)}: it paints ${outside.color} outside .dark, the variant is not conditional`);
  }
  return problems;
}

/**
 * Judge the child painted through an undefined CSS variable.
 *
 * The declaration is `var(--undefined, #8b5cf6)`: the fallback is the only
 * thing that can paint it, so it is compared to an element painted with that
 * color directly rather than to a copy of the color in this script.
 *
 * @param {object[]} records Records of the cases
 * @returns {string[]} Problems, empty when the fallback paints
 */
function judgeFallback(records) {
  const test = FALLBACK_CASES[0];
  const subject = slotOf(records, test.id, "subject");
  const reference = slotOf(records, test.id, "reference");
  const subjectColor = parseColor(subject.color);
  const referenceColor = parseColor(reference.color);
  if (!referenceColor || referenceColor[3] === 0) {
    return [`${label(reference)}: the reference for the fallback color computes nothing`];
  }
  if (!subjectColor || subjectColor[3] === 0) {
    return [`${label(subject)}: the declaration with an undefined variable paints nothing`];
  }
  if (!sameColor(subjectColor, referenceColor)) {
    return [
      `${label(subject)}: computes ${subject.color} where the fallback ${reference.color} of the declaration ` +
        `is expected`,
    ];
  }
  const distance = pixelDistance(subject.pixel, [reference.pixel.r, reference.pixel.g, reference.pixel.b]);
  if (distance > PIXEL_TOLERANCE) {
    return [
      `${label(subject)}: the screenshot shows ${formatPixel(subject.pixel)} where the fallback color ` +
        `${formatPixel(reference.pixel)} is expected`,
    ];
  }
  return [];
}

/**
 * Judge a color pair before and after the pointer entered the element.
 *
 * @param {object[]} records Records rendered before the pointer moved
 * @param {object} hovered The subject read again with the pointer over it
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when the hover rule applied at the right time
 */
function judgeHoverColor(records, hovered, test) {
  const problems = [];
  const subject = slotOf(records, test.id, "subject");
  const base = slotOf(records, test.id, "base");
  const active = slotOf(records, test.id, "active");
  const baseColor = parseColor(base.color);
  const activeColor = parseColor(active.color);
  if (!baseColor || !activeColor || baseColor[3] === 0 || activeColor[3] === 0) {
    return [`case ${test.id}: a reference color is missing from the build (${base.color}, ${active.color})`];
  }
  const before = parseColor(subject.color);
  if (!before) return [`case ${test.id} before hover: the subject computes no background color`];
  if (!sameColor(before, baseColor)) {
    problems.push(`case ${test.id} before hover: computes ${subject.color} where ${base.classes} is expected`);
  }
  if (sameColor(before, activeColor)) {
    problems.push(`case ${test.id} before hover: already paints ${active.classes} without the pointer`);
  }
  const after = parseColor(hovered?.color);
  if (!after) return [...problems, `case ${test.id} after hover: the subject computes no background color`];
  if (!sameColor(after, activeColor)) {
    problems.push(
      `case ${test.id} after hover: computes ${hovered.color} where ${active.classes} (${formatChannels(activeColor)}) ` +
        `is expected`,
    );
  }
  return problems;
}

/**
 * Judge the transform that a hover must apply.
 *
 * @param {object[]} records Records rendered before the pointer moved
 * @param {object} hovered The subject read again with the pointer over it
 * @param {object} test Case definition
 * @returns {string[]} Problems, empty when the box grew by the factor
 */
function judgeHoverScale(records, hovered, test) {
  const problems = [];
  const initial = slotOf(records, test.id, "subject");
  if (Math.abs(initial.box.width - SLOT_SIZE) > GEOMETRY_TOLERANCE) {
    problems.push(
      `case ${test.id} before hover: the box is ${initial.box.width.toFixed(2)}px wide, the hover rule leaked`,
    );
  }
  const expected = SLOT_SIZE * test.factor;
  if (Math.abs(hovered.box.width - expected) > GEOMETRY_TOLERANCE) {
    problems.push(
      `case ${test.id} after hover: the box is ${hovered.box.width.toFixed(2)}px wide where ${expected.toFixed(2)}px ` +
        `is expected`,
    );
  }
  return problems;
}

/**
 * Judge the responsive box at one viewport width.
 *
 * @param {object[]} records Records of the case
 * @param {number} width Viewport width the records were rendered at
 * @returns {string[]} Problems, empty when the expected breakpoint is active
 */
function judgeBreakpoint(records, width) {
  const subject = slotOf(records, "breakpoints", "subject");
  const expectedSlot = width >= 1024 ? "lg" : width >= 768 ? "md" : "base";
  const expected = slotOf(records, "breakpoints", expectedSlot);
  const subjectColor = parseColor(subject.color);
  const expectedColor = parseColor(expected.color);
  if (!expectedColor || expectedColor[3] === 0) {
    return [`breakpoints at ${width}px: the reference ${expected.classes || expected.slot} computes nothing`];
  }
  if (!subjectColor || subjectColor[3] === 0) {
    return [`breakpoints at ${width}px: the responsive box paints nothing`];
  }
  if (!sameColor(subjectColor, expectedColor)) {
    return [
      `breakpoints at ${width}px: computes ${subject.color} where ${expected.classes} ` +
        `(${expected.color}) is expected`,
    ];
  }
  return [];
}

/**
 * Judge the control phase: the engine must not be able to paint these.
 *
 * A control that paints is a note, not a failure: on a newer engine it is
 * expected. The failure is a control that paints while the polyfilled probe is
 * also the only reason the real swatches paint — which this phase can not
 * observe, hence the note.
 *
 * @param {object[]} records Records of the control stylesheet
 * @returns {{problems: string[], notes: string[]}} Findings
 */
function judgeControl(records) {
  const problems = [];
  const notes = [];
  if (records.length === 0) {
    problems.push("the control stylesheet produced no rule, the engine parsed nothing at all");
  }
  const supported = [];
  for (const record of records) {
    if (!record.pixel) problems.push(`${label(record)}: no pixel was sampled, the screenshot does not cover it`);
    const painted = (record.color && !isTransparent(record.color)) || record.image;
    if (painted) supported.push(record);
  }
  for (const record of supported) {
    notes.push(
      `${record.case} paints natively (computed ${record.color || record.image}, screenshot ` +
        `${record.pixel ? formatPixel(record.pixel) : "not sampled"}): the polyfill is not what makes it paint`,
    );
  }
  if (supported.length === 0) {
    notes.push(
      "the engine drops raw oklab(), color-mix() and nested rules: the polyfills are what make the page paint",
    );
  }
  return { problems, notes };
}

/**
 * Run one phase: load its stylesheets, render its cases, judge them.
 *
 * @param {string} workDir Scratch directory of this run
 * @param {string} name Phase name
 * @param {{ file: string, css: string }[]} sheets Stylesheets to load
 * @param {object} phase Phase definition
 * @returns {Promise<{problems: string[], notes: string[]}>} Findings
 */
async function runPhase(workDir, name, sheets, phase) {
  const win = await openPhase(workDir, name, sheets);
  const problems = [];
  const notes = [];
  try {
    if (phase.widths) {
      for (const width of phase.widths) {
        win.setContentSize(width, WINDOW_HEIGHT);
        await nextFrames(win, 2);
        const result = await render(win, phase.cases, CENTER);
        problems.push(...judgeBreakpoint(result.records, result.viewport.width));
        notes.push(
          `breakpoints at ${result.viewport.width}px: ${slotOf(result.records, "breakpoints", "subject").color}`,
        );
      }
      return { problems, notes };
    }

    if (phase.themes) {
      const themed = {};
      for (const theme of phase.themes) {
        if (theme === "dark") {
          await win.webContents.executeJavaScript('document.documentElement.classList.add("dark")', true);
          await nextFrames(win, 2);
        }
        themed[theme] = await render(win, phase.cases, phase.points ?? CENTER);
        if (phase.judge) problems.push(...phase.judge(themed[theme]));
      }
      if (phase.afterThemes) problems.push(...phase.afterThemes(themed));
      return { problems, notes };
    }

    const before = await render(win, phase.cases, phase.points ?? CENTER);
    if (phase.report) {
      const verdict = phase.report(before);
      problems.push(...verdict.problems);
      notes.push(...verdict.notes);
    }
    if (phase.judge) problems.push(...phase.judge(before));
    if (phase.hover) {
      for (const test of phase.cases) {
        const subject = slotOf(before.records, test.id, "subject");
        const x = Math.round(subject.box.x + subject.box.width / 2);
        const y = Math.round(subject.box.y + subject.box.height / 2);
        win.webContents.sendInputEvent({ type: "mouseMove", x, y });
        await new Promise((resolve) => setTimeout(resolve, 120));
        await nextFrames(win, 2);
        // The element is read, not re-rendered: replacing the node under the
        // pointer would drop the hover state the phase is about.
        const hovered = await readElement(win, test.id, "subject");
        if (!hovered) {
          problems.push(`case ${test.id}: the subject is gone after the pointer moved`);
          continue;
        }
        if (test.kind === "scale") problems.push(...judgeHoverScale(before.records, hovered, test));
        else problems.push(...judgeHoverColor(before.records, hovered, test));
        notes.push(`${test.id}: hovered the subject at ${x},${y}`);
      }
    }
    return { problems, notes };
  } finally {
    win.destroy();
  }
}

/**
 * Run every phase and report.
 */
async function main() {
  const options = parseArgs(process.argv.slice(2));
  const sources = options.css ?? [frontendBuild];

  let sheets;
  try {
    sheets = sources.flatMap((source) => collectSheets(path.resolve(source)));
  } catch (error) {
    console.error(`[compat] ${error instanceof Error ? error.message : String(error)}`);
    app.exit(2);
    return;
  }

  const phases = [
    {
      name: "control",
      sheets: [{ file: "control.css", css: CONTROL_CSS }],
      cases: [
        { id: "raw-oklab", slots: [{ name: "subject", classes: "control-oklab" }] },
        { id: "raw-color-mix", slots: [{ name: "subject", classes: "control-color-mix" }] },
        {
          id: "raw-nesting",
          slots: [{ name: "subject", parentClasses: "control-nest", classes: "control-nest-child" }],
        },
      ],
      report: (result) => judgeControl(result.records),
    },
    { name: "colors", cases: [...COLOR_CASES], judge: (result) => result.records.flatMap(judgeDeclaredColor) },
    {
      name: "gradients",
      cases: [...GRADIENT_CASES],
      points: GRADIENT_POINTS,
      judge: (result) => result.records.flatMap(judgeGradient),
    },
    {
      name: "opacity",
      cases: [...OPACITY_CASES, ...OPACITY_DECORATION_CASES],
      themes: ["light", "dark"],
      judge: (result) =>
        [...OPACITY_CASES, ...OPACITY_DECORATION_CASES].flatMap((test) => judgeOpacity(result.records, test)),
      // A theme that never reaches the paint would make the dark pass above
      // vacuous: both sides would be the light color and still agree.
      afterThemes: (themed) => {
        const light = slotOf(themed.light.records, "primary-50", "reference");
        const dark = slotOf(themed.dark.records, "primary-50", "reference");
        const lightColor = parseColor(light.color);
        const darkColor = parseColor(dark.color);
        if (!lightColor || !darkColor) return ["the primary reference computes no color in one of the themes"];
        if (sameColor(lightColor, darkColor)) {
          return [`the dark theme does not reach bg-primary: it computes ${dark.color} in both themes`];
        }
        return [];
      },
    },
    {
      name: "transforms",
      cases: [...TRANSFORM_CASES],
      points: CORNERS,
      judge: (result) => TRANSFORM_CASES.flatMap((test) => judgeTransform(result.records, test)),
    },
    {
      name: "nesting",
      cases: [...NESTING_CASES, ...FALLBACK_CASES],
      judge: (result) => [
        ...NESTING_CASES.flatMap((test) => judgeNesting(result.records, test)),
        ...judgeFallback(result.records),
      ],
    },
    {
      name: "variants",
      cases: [...VARIANT_CASES],
      judge: (result) => VARIANT_CASES.flatMap((test) => judgeVariant(result.records, test)),
    },
    {
      name: "hover",
      cases: [...HOVER_CASES],
      hover: true,
    },
    { name: "breakpoints", cases: [...BREAKPOINT_CASES], widths: [640, 900, 1280] },
  ];

  await app.whenReady();
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "alasio-compat-"));
  const failures = [];
  const notes = [];

  try {
    for (const phase of phases) {
      try {
        const verdict = await runPhase(workDir, phase.name, phase.sheets ?? sheets, phase);
        if (verdict.problems.length > 0) {
          failures.push(...verdict.problems);
          console.log(`[compat] ${phase.name}: FAILED (${verdict.problems.length} problem(s))`);
        } else {
          console.log(`[compat] ${phase.name}: PASSED`);
        }
        for (const note of verdict.notes) notes.push(`${phase.name}: ${note}`);
      } catch (error) {
        failures.push(`${phase.name}: ${error instanceof Error ? error.message : String(error)}`);
        console.log(`[compat] ${phase.name}: FAILED (${error instanceof Error ? error.message : error})`);
      }
    }
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }

  for (const note of notes) console.log(`[compat]   ${note}`);

  if (failures.length > 0) {
    console.error("[compat] FAILED");
    for (const failure of failures) console.error(`[compat]   ${failure}`);
    app.exit(1);
    return;
  }

  console.log("[compat] OK: every compatibility feature renders in the engine the client ships");
  app.exit(0);
}

// Match the app: no GPU, no sandbox (the page only needs styles and layout).
// A screenshot is only comparable to a number when the rasterizer is told what
// to produce: no GPU path with a different rounding, and one sRGB profile
// whatever the runner's display thinks it is.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-http-cache");
app.commandLine.appendSwitch("force-color-profile", "srgb");
app.commandLine.appendSwitch("force-device-scale-factor", "1");

app.on("window-all-closed", () => {});

main().catch((error) => {
  console.error("[compat] the smoke test itself failed:", error);
  app.exit(1);
});
