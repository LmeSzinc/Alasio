/**
 * Tests for pinToAnchor().
 *
 * The helper pins the element the user clicked while a toggle grows or shrinks
 * the page around it: a scroll port can not rest past its end, and a browser may
 * move the viewport on its own while an update lands, so the offset the page
 * keeps is not always the one the user was at.
 */
import { afterEach, describe, expect, it } from "vitest";
import { pinToAnchor } from "./scroll.svelte";

/** A rect of jsdom, which has no layout: only `top` is read by the helper */
function rectAt(top: number): DOMRect {
  return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top } as DOMRect;
}

/**
 * An anchor inside a scroll port, both faked for a layout jsdom does not have.
 *
 * `tops` are the values the rect of the anchor reports, one per read, so a test
 * says where the anchor travelled between the two measurements of the helper.
 * The port keeps the offset written to it.
 */
function scrollPort(tops: number[], scrollTop: number) {
  const port = document.createElement("div");
  port.style.overflowY = "auto";
  let offset = scrollTop;
  Object.defineProperty(port, "scrollTop", {
    configurable: true,
    get: () => offset,
    set: (value: number) => (offset = value),
  });

  const anchor = document.createElement("button");
  port.appendChild(anchor);
  document.body.appendChild(port);

  let read = 0;
  Object.defineProperty(anchor, "getBoundingClientRect", {
    configurable: true,
    value: () => rectAt(tops[Math.min(read++, tops.length - 1)]),
  });

  return {
    port,
    anchor,
    get offset() {
      return offset;
    },
    setOffset(value: number) {
      offset = value;
    },
  };
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("TestPinToAnchor", () => {
  it("keeps the offset when the anchor does not move", async () => {
    // Rows that land below the header push nothing around it: the page keeps
    // the offset the user scrolled to
    const page = scrollPort([40, 40], 1200);

    await pinToAnchor(page.anchor, () => {});

    expect(page.offset).toBe(1200);
  });

  it("scrolls back by however far the anchor moved", async () => {
    // Rows inserted above the anchor push it down by 20: the port follows by
    // the same amount, so the anchor stays under the pointer
    const page = scrollPort([40, 60], 1200);

    await pinToAnchor(page.anchor, () => {});

    expect(page.offset).toBe(1220);
  });

  it("puts a viewport the browser moved on its own back", async () => {
    // Scroll anchoring drags the port toward the new end while the update
    // lands, which lifts the anchor on screen: the port follows by the same
    // amount, so the control lands where the user clicked it
    const page = scrollPort([40, 40 - 275], 2644);

    await pinToAnchor(page.anchor, () => page.setOffset(2919));

    expect(page.offset).toBe(2644);
  });

  it("leaves a viewport the browser moved while the anchor stayed put", async () => {
    // There is nothing to correct: the clicked control is still under the
    // pointer, whatever the port did behind it
    const page = scrollPort([40, 40], 2644);

    await pinToAnchor(page.anchor, () => page.setOffset(2919));

    expect(page.offset).toBe(2919);
  });

  it("runs the change when the anchor has no scroll port", async () => {
    // Nothing to pin, but the change itself must still happen
    const anchor = document.createElement("button");
    document.body.appendChild(anchor);
    let changed = false;

    await pinToAnchor(anchor, () => (changed = true));

    expect(changed).toBe(true);
  });

  it("runs the change without an anchor", async () => {
    let changed = false;

    await pinToAnchor(null, () => (changed = true));

    expect(changed).toBe(true);
  });
});
