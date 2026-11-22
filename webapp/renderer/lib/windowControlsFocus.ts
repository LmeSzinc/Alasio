// Focus guard for the window controls of the frameless window.
//
// When the window is re-activated while nothing in the page is focused,
// Chromium does not hand the focus back to nothing: it runs Blink's *initial
// focus*, which ignores the previous focus and takes the first sequentially
// focusable element of the focused frame (`FocusController::SetInitialFocus()`
// -> `AdvanceFocusInDocumentOrder(..., initial_focus=true)`; the starting point
// of the last focus is deliberately skipped in that mode). Closing DevTools is
// exactly that situation — the DevTools view holds the focus while it is open,
// so the page has nothing focused when the view goes away — and in this window
// the first focusable element is the first button of the window controls (hide
// to tray). The button then paints its `focus-visible` ring even though the
// user never focused it. DevTools is normally opened with a key combination, so
// the focus is treated as keyboard focus and the ring is drawn; a page cannot
// opt out of the initial focus itself, only undo it afterwards.
//
// Only the *stray* focus is dropped: the one that arrives right after the
// window regained focus, for an element that was not focused before the window
// lost it and without any pointer or key input since then. A control the user
// focused by click or Tab is kept, so real focus is never taken away.
//
// Cost of watching input: key and pointer listeners are only registered while
// the window is inactive (from its `blur` until the activation is decided), so
// a key press in a focused window runs no listener of this module at all. Note
// that the page this guard belongs to is the shell: keystrokes typed into the
// embedded frontend are dispatched in the iframe's document and never reach
// these listeners even while they are registered.
//
// Pure module on purpose: it carries no runes, so its lifetime is plain DOM.

/**
 * Drops the focus Chromium hands to the window controls on re-activation.
 *
 * Args:
 *   group: Element wrapping the window control buttons, i.e. the only subtree
 *     the stray focus is taken away from
 *
 * Returns:
 *   Removes every listener again
 */
export function guardWindowControlsFocus(group: HTMLElement): () => void {
  // Element that had the focus when the window lost it. Chromium restores that
  // one on the way back, and that restoration is kept.
  let focusedBeforeBlur: Element | null = null;
  // Set when the window regained focus: the next focus inside the group cannot
  // be told apart from a focus the user asked for without this flag.
  let reactivated = false;
  // Set by any pointer or key input since the window lost the focus. A user
  // interaction is what makes the focus that follows it the user's own, but it
  // can arrive either side of the window `focus` event: clicking a control of
  // an inactive window may deliver the press before or after the activation,
  // so the flag is not cleared when the window comes back.
  let userInput = false;
  // Whether the input listeners are registered right now. They exist only for
  // the span of an activation (see the module comment).
  let inputListening = false;

  const startWatchingInput = () => {
    if (inputListening) return;
    inputListening = true;
    window.addEventListener("pointerdown", handleUserInput, true);
    window.addEventListener("keydown", handleUserInput, true);
  };

  const stopWatchingInput = () => {
    if (!inputListening) return;
    inputListening = false;
    window.removeEventListener("pointerdown", handleUserInput, true);
    window.removeEventListener("keydown", handleUserInput, true);
  };

  const handleWindowBlur = () => {
    focusedBeforeBlur = document.activeElement;
    userInput = false;
    // A press or a key is what makes the focus that follows the user's own, and
    // they can only arrive while the window is inactive.
    startWatchingInput();
  };

  const handleWindowFocus = () => {
    reactivated = true;
  };

  const handleUserInput = () => {
    userInput = true;
    // The question this activation was waiting an answer for is settled: from
    // here on every focus is kept, so the listeners can go.
    stopWatchingInput();
  };

  const handleFocusIn = (event: FocusEvent) => {
    // Not the focus of an activation: the guard has nothing to decide, and the
    // activation (if any) is still waiting for input.
    if (!reactivated) return;
    reactivated = false;
    stopWatchingInput();
    // A pointer press or a key press asked for this focus.
    if (userInput) return;
    // The window came back to where it was: this is the user's own focus, which
    // Chromium restored for them.
    if (event.target === focusedBeforeBlur) return;
    if (!(event.target instanceof HTMLElement)) return;

    // Hand the focus back to the body instead of blurring the control: a plain
    // blur() keeps Blink's sequential focus navigation starting point on the
    // control, so the next Tab would skip it. The body is only focusable with
    // a tabindex, and removing it again leaves the starting point at the top of
    // the document, where Tab reaches the first control.
    const body = event.target.ownerDocument?.body;
    if (!body) {
      event.target.blur();
      return;
    }
    body.tabIndex = -1;
    body.focus();
    body.removeAttribute("tabindex");
  };

  window.addEventListener("blur", handleWindowBlur);
  window.addEventListener("focus", handleWindowFocus);
  group.addEventListener("focusin", handleFocusIn);

  return () => {
    window.removeEventListener("blur", handleWindowBlur);
    window.removeEventListener("focus", handleWindowFocus);
    group.removeEventListener("focusin", handleFocusIn);
    stopWatchingInput();
  };
}
