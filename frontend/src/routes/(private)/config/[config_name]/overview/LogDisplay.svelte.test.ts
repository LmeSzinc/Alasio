/**
 * Tests for how the log viewport is fitted into its card (LogDisplay.svelte).
 *
 * The log is read from the bottom, so the viewport scrolls to the end of the
 * content. Both edges then have to land on a line boundary: a top edge cut
 * mid-line shows a sliver of glyphs instead of the line, and a bottom edge above
 * the last line leaves blank space to scroll into. The entries are a whole
 * number of line boxes tall, so the component only has to make the viewport a
 * whole number of lines tall: it measures one line on a hidden probe and splits
 * the rest of the last line between the two edge gaps.
 *
 * The tests fake that layout, since jsdom lays nothing out: the probe has a line
 * height, the entries have whole lines, the card has a height, and the scroll
 * offset is clamped the way the browser clamps it. They also count the box
 * measurements, because the point of the geometry is that following a new entry
 * costs nothing and only a resize is measured.
 *
 * An empty log shows its hint as an overlay of the card, so the hint carries no
 * line boxes and stays out of the geometry the card fits: it holds still while
 * the card resizes, and the fit is the same whether the log is empty or filled.
 * Its tests are the second block below.
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushEffects } from "$lib/test-utils/flush-effects";
import LogDisplay from "./LogDisplay.svelte";
import type { LogDataProps } from "./types";

/**
 * The row height the layout produces: the 1.125rem leading of a log row
 * (`text-xs` would be 1rem, the leading adds the breathing room instead of
 * vertical padding). Measured on the probe, never assumed by the component.
 */
const PITCH = 18;
/** Gap kept between the log and the card edges, the value the component uses */
const EDGE_GAP = 10;
/** Height of the card's border box, not a whole number of lines */
const CARD_HEIGHT = 600.6;

/**
 * jsdom has no ResizeObserver; the stub records the callbacks so a test can
 * report a card resize by hand. Like the browser it reports entries, not a bare
 * call: bits-ui's pooled observer dispatches on `entry.target` and iterates the
 * list, so a callback without entries would throw there.
 */
type ResizeCallback = (entries: { target: Element }[]) => void;

class ResizeObserverStub {
  static callbacks: ResizeCallback[] = [];
  static observed = new Map<ResizeCallback, Element[]>();
  #callback: ResizeCallback;

  constructor(callback: ResizeCallback) {
    this.#callback = callback;
    ResizeObserverStub.callbacks.push(callback);
    ResizeObserverStub.observed.set(callback, []);
  }

  observe(element: Element) {
    ResizeObserverStub.observed.get(this.#callback)?.push(element);
  }

  unobserve() {}

  disconnect() {
    ResizeObserverStub.callbacks = ResizeObserverStub.callbacks.filter((callback) => callback !== this.#callback);
    ResizeObserverStub.observed.delete(this.#callback);
  }

  /** Reports a resize to every live observer, with the elements it observes */
  static trigger() {
    for (const callback of [...ResizeObserverStub.callbacks]) {
      callback((ResizeObserverStub.observed.get(callback) ?? []).map((target) => ({ target })));
    }
  }
}

/** Mounted component handles, unmounted after every test */
let mounted: any[] = [];

/** One message per entry, one line per string of `lines` */
function makeLogs(lines: number[]): LogDataProps[] {
  return lines.map((count, index) => ({
    t: 0,
    l: "INFO",
    m: Array.from({ length: count }, (_, line) => `line ${index}.${line}`).join("\n"),
  }));
}

/** The card the log is fitted into */
function card(target: HTMLElement): HTMLElement {
  return target.querySelector<HTMLElement>('[data-slot="log-card"]')!;
}

/** The viewport of the scroll area, its scroller */
function viewport(target: HTMLElement): HTMLElement {
  return target.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]')!;
}

/** The overlay that carries the empty hint */
function emptyHint(target: HTMLElement): HTMLElement | null {
  return target.querySelector<HTMLElement>('[data-slot="log-empty"]');
}

/** Top offset class of the hint: one spacing step down from the card edge */
const HINT_TOP_CLASS = "top-4";

/** A gap the component set on the card, in px */
function edgeGap(target: HTMLElement, edge: "Top" | "Bottom"): number {
  return Number.parseFloat(card(target).style[`padding${edge}`]) || 0;
}

/** Number of box measurements taken so far */
let measurements = 0;

/**
 * Gives the mounted log a layout model: a probe of one line, a card of a fixed
 * height whose two gaps are its padding, a viewport of the card minus the gaps,
 * a content of whole lines, and a scroll offset clamped the way the browser
 * clamps it.
 *
 * Args:
 *     target (HTMLElement): Mount target of the component
 *     layout (object): The card height and the lines the entries occupy; a test
 *         changes both to emulate a resize that re-wraps the messages
 *
 * Returns:
 *     object: The same layout object, so a test can resize it
 */
function fakeLayout(target: HTMLElement, layout: { height: number; lines: number }): { height: number; lines: number } {
  const box = (height: number): DOMRect => ({ height }) as DOMRect;
  const viewportHeight = () => layout.height - edgeGap(target, "Top") - edgeGap(target, "Bottom");
  const contentHeight = () => PITCH * layout.lines;

  card(target).getBoundingClientRect = () => {
    measurements += 1;
    return box(layout.height);
  };
  card(target).querySelector<HTMLElement>('[data-slot="log-line-probe"]')!.getBoundingClientRect = () => {
    measurements += 1;
    return box(PITCH);
  };

  const view = viewport(target);
  view.getBoundingClientRect = () => {
    measurements += 1;
    return box(viewportHeight());
  };
  let offset = 0;
  Object.defineProperties(view, {
    scrollHeight: { get: () => contentHeight() },
    clientHeight: { get: () => viewportHeight() },
    scrollTop: {
      get: () => offset,
      set: (value: number) => {
        offset = Math.max(0, Math.min(value, contentHeight() - viewportHeight()));
      },
    },
  });
  return layout;
}

/**
 * Mounts the log with the given entries and fakes the layout before the first
 * auto scroll runs.
 */
async function mountLog(lines: number[], cardHeight = CARD_HEIGHT) {
  const target = document.createElement("div");
  document.body.appendChild(target);
  const data = $state(makeLogs(lines));
  const totalLines = data.reduce((sum, log) => sum + log.m.split("\n").length, 0);
  mounted.push(mount(LogDisplay, { target, props: { data } }));
  const layout = fakeLayout(target, { height: cardHeight, lines: totalLines });
  await settle();
  return { target, data, layout };
}

/** Unmounts every mounted log so a test can mount a second one */
function unmountAll() {
  for (const component of mounted) {
    unmount(component);
  }
  mounted = [];
  document.body.innerHTML = "";
}

/** Runs the pending effects and the frames the auto scroll waits for */
async function settle() {
  for (let i = 0; i < 2; i++) {
    await flushEffects();
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  await flushEffects();
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  ResizeObserverStub.callbacks = [];
  measurements = 0;
  // The card carries no border in these tests; everything else keeps the real
  // style (the scroll area reads it as well)
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element: Element) =>
    element.getAttribute("data-slot") === "log-card"
      ? ({ borderTopWidth: "0px", borderBottomWidth: "0px" } as CSSStyleDeclaration)
      : computed(element),
  );
});

afterEach(() => {
  unmountAll();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("TestLogFitToLines", () => {
  it("fits the viewport to whole lines and splits the rest of the last one", async () => {
    const { target } = await mountLog(Array.from({ length: 40 }, () => 2));
    const view = viewport(target);
    const rest = (CARD_HEIGHT - 2 * EDGE_GAP) % PITCH;

    // The gaps carry the lines the card cannot hold, half each, so they stay even
    expect(edgeGap(target, "Top")).toBeCloseTo(EDGE_GAP + rest / 2, 6);
    expect(edgeGap(target, "Bottom")).toBeCloseTo(EDGE_GAP + rest / 2, 6);
    // The viewport is a whole number of lines tall ...
    expect(view.clientHeight % PITCH).toBeCloseTo(0, 6);
    // ... so with whole-line content it starts on a line boundary at the bottom
    expect(view.scrollTop).toBe(view.scrollHeight - view.clientHeight);
    expect((view.scrollHeight - view.scrollTop) % PITCH).toBeCloseTo(0, 6);
  });

  it("keeps the gaps when the card already happens to be whole lines tall", async () => {
    // 2 * EDGE_GAP + 36 lines
    const { target } = await mountLog(
      Array.from({ length: 36 }, () => 1),
      2 * EDGE_GAP + 36 * PITCH,
    );

    expect(edgeGap(target, "Top")).toBeCloseTo(EDGE_GAP, 6);
    expect(edgeGap(target, "Bottom")).toBeCloseTo(EDGE_GAP, 6);
  });

  it("measures the line on the probe, not on the first entry", async () => {
    // An empty message still occupies a line box; deriving the line height from
    // the entries would have nothing to measure there
    const data = $state<LogDataProps[]>([
      { t: 0, l: "INFO", m: "" },
      { t: 0, l: "INFO", m: "line 1" },
    ]);
    const target = document.createElement("div");
    document.body.appendChild(target);
    mounted.push(mount(LogDisplay, { target, props: { data } }));
    fakeLayout(target, { height: CARD_HEIGHT, lines: 2 });
    await settle();

    expect(edgeGap(target, "Top")).toBeCloseTo(EDGE_GAP + ((CARD_HEIGHT - 2 * EDGE_GAP) % PITCH) / 2, 6);
  });

  it("leaves a short log at the top of its gaps", async () => {
    const { target } = await mountLog([1, 2, 1, 1, 1]);
    const view = viewport(target);

    expect(view.scrollTop).toBe(0);
    expect(edgeGap(target, "Top")).toBeGreaterThanOrEqual(EDGE_GAP);
    expect(edgeGap(target, "Bottom")).toBeGreaterThanOrEqual(EDGE_GAP);
  });

  it("follows the card height when the card is resized", async () => {
    const { target, layout } = await mountLog(Array.from({ length: 40 }, () => 2));

    layout.height = 720.25;
    ResizeObserverStub.trigger();
    await settle();

    const view = viewport(target);
    expect(edgeGap(target, "Top")).toBeCloseTo(EDGE_GAP + ((720.25 - 2 * EDGE_GAP) % PITCH) / 2, 6);
    expect(view.clientHeight % PITCH).toBeCloseTo(0, 6);
    expect(view.scrollTop).toBe(view.scrollHeight - view.clientHeight);
  });

  it("follows the newest entry when a resize re-wraps the messages", async () => {
    const { target, layout } = await mountLog(Array.from({ length: 40 }, () => 2));
    const view = viewport(target);

    // A narrower card wraps the same messages into more lines
    layout.height = 520.5;
    layout.lines = 120;
    ResizeObserverStub.trigger();
    await settle();

    expect(view.scrollTop).toBe(view.scrollHeight - view.clientHeight);
    expect(view.clientHeight % PITCH).toBeCloseTo(0, 6);
  });

  it("measures nothing while new entries arrive", async () => {
    const { target, data, layout } = await mountLog(Array.from({ length: 40 }, () => 2));
    const firstFit = measurements;
    expect(firstFit).toBeLessThanOrEqual(2);

    data.push(...makeLogs([2, 3, 1]));
    layout.lines += 6;
    await settle();

    // Following the newest entry is a scroll, not a measurement
    expect(measurements).toBe(firstFit);
    expect(viewport(target).scrollTop).toBe(viewport(target).scrollHeight - viewport(target).clientHeight);
  });

  it("costs the same whether the log holds ten entries or a thousand", async () => {
    await mountLog(Array.from({ length: 10 }, () => 2));
    const few = measurements;
    unmountAll();
    measurements = 0;

    await mountLog(Array.from({ length: 1000 }, () => 2));

    expect(few).toBeLessThanOrEqual(2);
    expect(measurements).toBe(few);
  });
});

describe("TestLogEmptyState", () => {
  it("shows the hint as an overlay, not as content of the log", async () => {
    const { target } = await mountLog([]);
    const hint = emptyHint(target);

    expect(hint).not.toBeNull();
    // Out of the scroll content: the hint is nobody's line box
    expect(viewport(target).contains(hint)).toBe(false);
    expect(target.querySelector('[data-slot="log-content"]')!.textContent).toBe("");
    // An overlay at the top of the card, where the first entry would begin
    expect(hint!.parentElement).toBe(card(target));
    expect(hint!.classList.contains("absolute")).toBe(true);
    expect(hint!.classList.contains(HINT_TOP_CLASS)).toBe(true);
  });

  it("keeps the hint in place while the card resizes", async () => {
    const { target, layout } = await mountLog([]);
    const hint = emptyHint(target)!;
    const classes = hint.className;

    for (const height of [520.5, 720.25, 900.1]) {
      layout.height = height;
      ResizeObserverStub.trigger();
      await settle();

      // The card's gaps follow the height for the log's line grid; the hint is
      // anchored to the card, away from the pixels the fitted gaps move
      expect(hint.className).toBe(classes);
      expect(hint.parentElement).toBe(card(target));
      expect(edgeGap(target, "Top")).toBeCloseTo(EDGE_GAP + ((height - 2 * EDGE_GAP) % PITCH) / 2, 6);
    }
  });

  it("fits the card the same whether the log is empty or filled", async () => {
    const { target: emptyTarget } = await mountLog([]);
    ResizeObserverStub.trigger();
    await settle();
    const emptyGaps = [edgeGap(emptyTarget, "Top"), edgeGap(emptyTarget, "Bottom")];
    unmountAll();

    const { target: filledTarget } = await mountLog([1, 1]);
    ResizeObserverStub.trigger();
    await settle();

    // The hint left the geometry: the same card gets the same gaps either way
    expect([edgeGap(filledTarget, "Top"), edgeGap(filledTarget, "Bottom")]).toEqual(emptyGaps);
  });

  it("drops the hint once entries arrive and brings it back when the log empties", async () => {
    const { target, data, layout } = await mountLog([]);
    expect(emptyHint(target)).not.toBeNull();

    layout.lines = 4;
    data.push(...makeLogs([2, 2]));
    await settle();
    expect(emptyHint(target)).toBeNull();

    data.splice(0);
    await settle();
    expect(emptyHint(target)).not.toBeNull();
  });
});
