/**
 * Tests for the focus guard of the window controls.
 *
 * A unit test cannot open DevTools: that is a Chromium feature owned by the
 * host window, not by the page. What the guard reacts to is the page side of
 * it, which is observable and therefore testable: while DevTools is open the
 * window reports `blur`; when it closes the page reports `focus`, and Blink's
 * initial focus hands the focus to the first sequentially focusable element,
 * which arrives as a `focusin` on the first window control. The cases below
 * replay that sequence — the real DevTools half is exercised by
 * `scripts/focus/browser-smoke.cjs`, which opens and closes it for real.
 *
 * The DOM is stubbed down to the two lookups the module performs (`window`,
 * `document.activeElement`, `HTMLElement`) instead of pulling in a DOM
 * implementation: the project keeps its renderer tests on plain node (see
 * vitest.config.mts), and jsdom would not reproduce `:focus-visible` or the
 * engine's initial focus anyway.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { guardWindowControlsFocus } from "./windowControlsFocus";

type Listener = (event: { target?: unknown }) => void;

/** Minimal event target, standing in for `window` and for DOM elements. */
class FakeEventTarget {
  private readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    let listeners = this.listeners.get(type);
    if (!listeners) {
      listeners = new Set();
      this.listeners.set(type, listeners);
    }
    listeners.add(listener);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  /**
   * Count registered listeners.
   *
   * Args:
   *   type (str): Event type, or every type when omitted
   *
   * Returns:
   *   int: Number of listeners
   */
  listenerCount(type?: string): number {
    if (type !== undefined) return this.listeners.get(type)?.size ?? 0;
    let total = 0;
    for (const listeners of this.listeners.values()) total += listeners.size;
    return total;
  }

  emit(type: string, event: { target?: unknown } = {}) {
    for (const listener of [...(this.listeners.get(type) ?? [])]) listener(event);
  }
}

class FakeDocument {
  activeElement: unknown = null;
  readonly body: FakeElement;

  constructor() {
    this.body = new FakeElement(this);
  }
}

class FakeElement extends FakeEventTarget {
  blurCount = 0;
  focusCount = 0;
  private readonly attributes = new Map<string, string>();

  constructor(readonly ownerDocument: FakeDocument) {
    super();
  }

  set tabIndex(value: number) {
    this.attributes.set("tabindex", String(value));
  }

  get tabIndex(): number {
    return Number(this.attributes.get("tabindex") ?? -1);
  }

  getAttribute(name: string): string | null {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  focus() {
    this.focusCount += 1;
    this.ownerDocument.activeElement = this;
  }

  /** Stands in for `HTMLElement.prototype.blur`, which also clears the focus. */
  blur() {
    this.blurCount += 1;
    if (this.ownerDocument.activeElement === this) {
      this.ownerDocument.activeElement = null;
    }
  }
}

describe("TestGuardWindowControlsFocus", () => {
  let fakeWindow: FakeEventTarget;
  let fakeDocument: FakeDocument;
  let controls: FakeElement;
  let hide: FakeElement;
  let minimize: FakeElement;
  let pageButton: FakeElement;
  let dispose: () => void;

  beforeEach(() => {
    fakeWindow = new FakeEventTarget();
    fakeDocument = new FakeDocument();
    vi.stubGlobal("window", fakeWindow);
    vi.stubGlobal("document", fakeDocument);
    vi.stubGlobal("HTMLElement", FakeElement);

    controls = new FakeElement(fakeDocument);
    hide = new FakeElement(fakeDocument);
    minimize = new FakeElement(fakeDocument);
    pageButton = new FakeElement(fakeDocument);

    dispose = guardWindowControlsFocus(controls as unknown as HTMLElement);
  });

  afterEach(() => {
    // Idempotent on purpose: the explicit cleanup in one case runs it twice.
    dispose();
    vi.unstubAllGlobals();
  });

  /** DevTools takes the focus: the page window reports `blur`. */
  function openDevTools(focused: FakeElement | null) {
    fakeDocument.activeElement = focused;
    fakeWindow.emit("blur");
  }

  /** DevTools closes: the page regains the focus and Blink focuses `target`. */
  function closeDevTools(target: FakeElement) {
    fakeWindow.emit("focus");
    fakeDocument.activeElement = target;
    controls.emit("focusin", { target });
  }

  it("drops the focus closing DevTools gives to the first window control", () => {
    // Nothing in the page was focused, so the engine starts from the top of
    // the document: the hide-to-tray button is the first focusable element.
    openDevTools(null);
    closeDevTools(hide);

    // The focus goes to the body, not to nothing: that keeps the next Tab at
    // the top of the document instead of after the control that was skipped.
    expect(fakeDocument.activeElement).toBe(fakeDocument.body);
    expect(fakeDocument.body.getAttribute("tabindex")).toBeNull();
  });

  it("keeps the focus the engine restores to the element that had it", () => {
    // Chromium restores the previous focus when there is one; that focus is
    // the user's and must survive.
    openDevTools(minimize);
    closeDevTools(minimize);

    expect(minimize.blurCount).toBe(0);
    expect(fakeDocument.activeElement).toBe(minimize);
  });

  it.each([
    // Clicking a control of an inactive window may deliver the press before
    // the activation or after it; both orders must keep the focus.
    ["pointerdown", "before the activation", true],
    ["pointerdown", "after the activation", false],
    ["keydown", "before the activation", true],
    ["keydown", "after the activation", false],
  ])("keeps a focus the user asked for with %s, press %s", (type, _order, before) => {
    // A click or a Tab onto a control is a deliberate focus, even when the
    // window was re-activated right before it.
    openDevTools(null);
    if (before) fakeWindow.emit(type);
    if (!before) {
      fakeWindow.emit("focus");
      fakeWindow.emit(type);
    }
    closeDevTools(hide);

    expect(hide.blurCount).toBe(0);
  });

  it("drops only the first focus of one activation", () => {
    openDevTools(null);
    closeDevTools(hide);
    // A focus the page moves by itself afterwards belongs to the page.
    controls.emit("focusin", { target: minimize });

    expect(minimize.blurCount).toBe(0);
  });

  it("leaves a focus alone when the window was not re-activated", () => {
    // Tabbing to a control (or clicking it) never involves the window losing
    // and regaining the focus, so the guard is not armed at all.
    controls.emit("focusin", { target: hide });

    expect(hide.blurCount).toBe(0);
  });

  it("leaves focus outside the window controls alone", () => {
    openDevTools(null);
    fakeWindow.emit("focus");
    // The guard listens on the controls, not on the window: a focus the page
    // receives elsewhere can never be taken away by it.
    fakeWindow.emit("focusin", { target: pageButton });

    expect(pageButton.blurCount).toBe(0);
    expect(fakeWindow.listenerCount("focusin")).toBe(0);
  });

  it("does not listen to input while the window is active", () => {
    // The per-keystroke cost has to be zero in the normal case, so the input
    // listeners only exist for the span of an activation.
    expect(fakeWindow.listenerCount("keydown")).toBe(0);
    expect(fakeWindow.listenerCount("pointerdown")).toBe(0);
  });

  it("watches input only while the window is inactive", () => {
    openDevTools(null);

    expect(fakeWindow.listenerCount("keydown")).toBe(1);
    expect(fakeWindow.listenerCount("pointerdown")).toBe(1);
  });

  it("stops watching input once the activation is decided", () => {
    openDevTools(null);
    closeDevTools(hide);

    expect(fakeWindow.listenerCount("keydown")).toBe(0);
    expect(fakeWindow.listenerCount("pointerdown")).toBe(0);
  });

  it("stops watching input after the first user input", () => {
    openDevTools(null);
    fakeWindow.emit("pointerdown");

    expect(fakeWindow.listenerCount("keydown")).toBe(0);
    expect(fakeWindow.listenerCount("pointerdown")).toBe(0);
  });

  it("guards every activation, not only the first", () => {
    openDevTools(null);
    closeDevTools(hide);
    openDevTools(null);
    closeDevTools(hide);

    expect(fakeDocument.body.focusCount).toBe(2);
  });

  it("ignores a focus target that is not an element", () => {
    openDevTools(null);
    fakeWindow.emit("focus");

    // Defensive branch: the engine only reports elements, but a stray event
    // must not throw and take the guard down with it.
    expect(() => controls.emit("focusin", { target: { nodeType: 1 } })).not.toThrow();
  });

  it("blurs the control when its document has no body", () => {
    // Defensive branch: a detached document has no body to hand the focus to.
    openDevTools(null);
    fakeWindow.emit("focus");
    const detached = new FakeElement({ activeElement: null } as unknown as FakeDocument);

    controls.emit("focusin", { target: detached });

    expect(detached.blurCount).toBe(1);
  });

  it("removes every listener when disposed", () => {
    dispose();

    expect(fakeWindow.listenerCount()).toBe(0);
    expect(controls.listenerCount()).toBe(0);

    openDevTools(null);
    closeDevTools(hide);

    expect(hide.blurCount).toBe(0);
  });

  it("removes the input listeners when disposed mid activation", () => {
    openDevTools(null);
    dispose();

    expect(fakeWindow.listenerCount()).toBe(0);
  });
});
