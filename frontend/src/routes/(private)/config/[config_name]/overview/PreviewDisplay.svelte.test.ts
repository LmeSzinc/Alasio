/**
 * Tests for the frame ordering rules of PreviewDisplay.svelte.
 *
 * The 16 byte header carries the capture timestamp, and that timestamp is the frame
 * identity — the wire format has no sequence number. So "which frame is newer" is answered
 * by comparing timestamps:
 *   - a decoded frame whose timestamp is older than the frame on screen is dropped (its
 *     bitmap is closed instead of drawn over the newer one),
 *   - a decode that fails after a newer frame was received must neither clear the canvas
 *     nor report an error,
 *   - the display state follows the frame that actually reaches the canvas.
 *
 * The debug page hands every card a single frame, so these orderings cannot be produced
 * there; they are covered here. jsdom has neither ImageBitmap nor a bitmap renderer
 * context, so `createImageBitmap` and `canvas.getContext("bitmaprenderer")` are stubbed:
 * the tests decide when each decode resolves (out of order on purpose) and record which
 * bitmaps were transferred to the canvas (drawn), closed (dropped) or replaced by null
 * (cleared).
 */
import { mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WORKER_STATE } from "$lib/components/aside/types";
import { flushEffects } from "$lib/test-utils/flush-effects";
import PreviewDisplay from "./PreviewDisplay.svelte";
import type { PreviewMode } from "./types";

/** A frame in the wire format: 8 byte ASCII header + big-endian ms timestamp + payload. */
function frame(header: string, timestamp: number, payloadBytes = 8): ArrayBuffer {
  const bytes = new Uint8Array(16 + payloadBytes);
  bytes.set(new TextEncoder().encode(header.padEnd(8, "\0").slice(0, 8)), 0);
  new DataView(bytes.buffer).setBigUint64(8, BigInt(timestamp));
  bytes.fill(0xab, 16);
  return bytes.buffer;
}

/** A frame carrying a preview image. */
function previewFrame(timestamp: number): ArrayBuffer {
  return frame("Preview_", timestamp);
}

/** The stop signal: header only, no image bytes. */
function stopFrame(timestamp: number): ArrayBuffer {
  return frame("PreviewS", timestamp, 0);
}

interface FakeBitmap {
  tag: string;
  width: number;
  height: number;
  close: ReturnType<typeof vi.fn>;
}

/** What `createImageBitmap` resolves to: only size and close() are used by the component. */
function makeBitmap(tag: string): FakeBitmap {
  return { tag, width: 640, height: 360, close: vi.fn() };
}

/** Pending decodes in call order; the tests resolve/reject them in any order they want. */
let decodes: { size: number; resolve: (bitmap: FakeBitmap) => void; reject: (error: unknown) => void }[] = [];
/** Bitmaps handed to the canvas in order; `null` is the clear call. */
let transfers: (FakeBitmap | null)[] = [];
let decoder: ReturnType<typeof vi.fn>;
/** Mounted component handles, unmounted after every test (a leak keeps effects alive). */
let mounted: unknown[] = [];

/** Mounts the component over a `$state` props object, which the test then mutates. */
function mountPreview() {
  const props = $state({
    config_name: "test",
    data: null as ArrayBuffer | null,
    previewMode: "normal" as PreviewMode,
    workerState: "running" as WORKER_STATE,
    onPreviewStart: () => {},
    onPreviewStop: () => {},
    onModeChange: (_mode: PreviewMode) => {},
  });
  const target = document.createElement("div");
  document.body.appendChild(target);
  mounted.push(mount(PreviewDisplay, { target, props }));
  const root = target.firstElementChild as HTMLElement;
  return { props, root, canvas: root.querySelector("canvas") as HTMLCanvasElement };
}

/** The canvas is shown only while it holds a frame and preview is enabled. */
function isCanvasVisible(canvas: HTMLCanvasElement): boolean {
  return canvas.classList.contains("block");
}

/** Every placeholder (error / stopped / disabled / not running / waiting) is an italic line. */
function hasPlaceholder(root: HTMLElement): boolean {
  return root.querySelector("div.italic") !== null;
}

beforeEach(() => {
  decodes = [];
  transfers = [];
  decoder = vi.fn(
    (blob: Blob) =>
      new Promise<FakeBitmap>((resolve, reject) => {
        decodes.push({ size: blob.size, resolve, reject });
      }),
  );
  vi.stubGlobal("createImageBitmap", decoder);
  const context = { transferFromImageBitmap: (bitmap: FakeBitmap | null) => transfers.push(bitmap) };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((contextId: string) =>
    contextId === "bitmaprenderer" ? context : null) as typeof HTMLCanvasElement.prototype.getContext);
});

afterEach(() => {
  for (const component of mounted) {
    unmount(component as never);
  }
  mounted = [];
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TestPreviewDisplayFrameOrder", () => {
  it("draws a frame and shows the canvas", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();

    // The payload reaches the decoder without the 16 byte header
    expect(decoder).toHaveBeenCalledTimes(1);
    expect(decodes).toHaveLength(1);
    expect(decodes[0].size).toBe(8);

    const first = makeBitmap("1000");
    decodes[0].resolve(first);
    await flushEffects();

    expect(transfers).toEqual([first]);
    // The backing store follows the frame size
    expect(canvas.width).toBe(640);
    expect(canvas.height).toBe(360);
    expect(isCanvasVisible(canvas)).toBe(true);
    expect(hasPlaceholder(root)).toBe(false);
    expect(first.close).not.toHaveBeenCalled();
  });

  it("draws every frame whose decode finishes in order", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();
    props.data = previewFrame(2000);
    await flushEffects();
    expect(decodes).toHaveLength(2);

    const first = makeBitmap("1000");
    decodes[0].resolve(first);
    await flushEffects();
    const second = makeBitmap("2000");
    decodes[1].resolve(second);
    await flushEffects();

    expect(transfers).toEqual([first, second]);
    expect(isCanvasVisible(canvas)).toBe(true);
    expect(hasPlaceholder(root)).toBe(false);
  });

  it("drops a decode that finishes after a newer frame was drawn", async () => {
    const { props, canvas } = mountPreview();
    // Frame 2000 arrives, then 3000: both decodes are in flight
    props.data = previewFrame(2000);
    await flushEffects();
    props.data = previewFrame(3000);
    await flushEffects();
    expect(decodes).toHaveLength(2);

    // The newer frame finishes first and is drawn
    const newer = makeBitmap("3000");
    decodes[1].resolve(newer);
    await flushEffects();
    expect(transfers).toEqual([newer]);
    expect(isCanvasVisible(canvas)).toBe(true);

    // The older frame finishes afterwards: older than the frame on screen, so it is
    // dropped instead of drawn over it
    const older = makeBitmap("2000");
    decodes[0].resolve(older);
    await flushEffects();
    expect(transfers).toEqual([newer]);
    expect(older.close).toHaveBeenCalledTimes(1);
    expect(isCanvasVisible(canvas)).toBe(true);
  });

  it("drops a frame that arrives after an older one is on screen", async () => {
    const { props, canvas } = mountPreview();
    props.data = previewFrame(2000);
    await flushEffects();
    const drawn = makeBitmap("2000");
    decodes[0].resolve(drawn);
    await flushEffects();
    expect(isCanvasVisible(canvas)).toBe(true);

    // A frame with an older timestamp (a re-sent cached frame) is not drawn
    props.data = previewFrame(1000);
    await flushEffects();
    const cached = makeBitmap("1000");
    decodes[1].resolve(cached);
    await flushEffects();

    expect(transfers).toEqual([drawn]);
    expect(cached.close).toHaveBeenCalledTimes(1);
    expect(isCanvasVisible(canvas)).toBe(true);
  });

  it("ignores a superseded frame that fails after the newer one was drawn", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(2000);
    await flushEffects();
    props.data = previewFrame(3000);
    await flushEffects();

    const newer = makeBitmap("3000");
    decodes[1].resolve(newer);
    await flushEffects();

    decodes[0].reject(new Error("undecodable"));
    await flushEffects();

    expect(transfers).toEqual([newer]);
    expect(isCanvasVisible(canvas)).toBe(true);
    expect(hasPlaceholder(root)).toBe(false);
  });

  it("shows the newer frame when a superseded frame fails first", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();
    props.data = previewFrame(2000);
    await flushEffects();

    // The older frame fails while nothing is on screen yet: its failure is reported (the
    // canvas is empty, so clearing it is invisible), and the newer frame takes over right
    // after — the card must end up showing it, not the error placeholder
    decodes[0].reject(new Error("undecodable"));
    await flushEffects();
    const newer = makeBitmap("2000");
    decodes[1].resolve(newer);
    await flushEffects();

    expect(transfers).toEqual([null, newer]);
    expect(isCanvasVisible(canvas)).toBe(true);
    expect(hasPlaceholder(root)).toBe(false);
  });

  it("reports an error when the newest frame cannot be decoded", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();

    decodes[0].reject(new Error("undecodable"));
    await flushEffects();

    expect(transfers).toEqual([null]);
    expect(isCanvasVisible(canvas)).toBe(false);
    expect(hasPlaceholder(root)).toBe(true);
  });

  it("clears the canvas on the stop signal", async () => {
    const { props, root, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();
    const drawn = makeBitmap("1000");
    decodes[0].resolve(drawn);
    await flushEffects();
    expect(isCanvasVisible(canvas)).toBe(true);

    props.data = stopFrame(1500);
    await flushEffects();

    // A stop signal carries no image, so nothing is decoded
    expect(decoder).toHaveBeenCalledTimes(1);
    expect(transfers).toEqual([drawn, null]);
    expect(isCanvasVisible(canvas)).toBe(false);
    expect(hasPlaceholder(root)).toBe(true);
  });

  it("accepts a frame that arrives after the canvas was cleared", async () => {
    const { props, canvas } = mountPreview();
    props.data = previewFrame(1000);
    await flushEffects();
    decodes[0].reject(new Error("undecodable"));
    await flushEffects();
    expect(isCanvasVisible(canvas)).toBe(false);

    // Nothing is on screen any more, so an older-than-the-failed-frame image is still drawn
    props.data = previewFrame(900);
    await flushEffects();
    const recovered = makeBitmap("900");
    decodes[1].resolve(recovered);
    await flushEffects();

    expect(transfers).toEqual([null, recovered]);
    expect(isCanvasVisible(canvas)).toBe(true);
  });

  it("labels the frame on screen with its own timestamp", async () => {
    const { props, root } = mountPreview();
    // Older than 10 seconds, so the overlay renders the age of the image
    props.data = previewFrame(Date.now() - 30_000);
    await flushEffects();
    decodes[0].resolve(makeBitmap("stale"));
    await flushEffects();
    expect(root.querySelector("div.font-mono")).not.toBeNull();

    // The stop signal empties the canvas: there is no image left to age
    props.data = stopFrame(Date.now());
    await flushEffects();
    expect(root.querySelector("div.font-mono")).toBeNull();
  });
});
