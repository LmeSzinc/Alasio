import { tick } from "svelte";

/**
 * Find the closest scrollable parent of an element.
 */
export function findScrollParent(node: HTMLElement | null) {
  if (!node) return null;
  let parent = node.parentElement;
  while (parent) {
    const style = getComputedStyle(parent);
    if (style.overflowY === "auto" || style.overflowY === "scroll") {
      return parent;
    }
    parent = parent.parentElement;
  }
  return null;
}

/**
 * Run a state change that grows or shrinks the page under `anchor`, and leave the
 * anchor where the user sees it.
 *
 * A scroll port can not rest past its end, so collapsing rows near the end of a
 * list pulls the whole page up by the height that disappeared: the browser
 * clamps `scrollTop` to the shorter content. Chrome also moves the viewport on
 * its own while such an update lands (scroll anchoring). Either way the moved
 * position is what the page keeps when the rows are opened again, so the
 * control the user clicked has jumped away from the pointer.
 *
 * Reading the rect of the anchor before the change and again after it, and
 * moving the port by the distance the anchor travelled on screen, pins that
 * anchor in place: the rows that appear grow away from it, and the rows that go
 * away take their space from below. Moving the port is measured against the
 * offset it has after the update, so a move the browser already made is not
 * counted twice.
 *
 * Writing back the offset recorded before the change would be the same
 * correction, as long as the anchor keeps its place in the document: the two
 * only differ when a row lands above the anchor, which moves it down while the
 * port stays where it was. Measuring the anchor covers both, and it still
 * degrades to the plain restore where the port really was at its end, because
 * the offset written back is then clamped to the shorter content anyway.
 *
 * @param anchor The element the user clicked, null when there is nothing to pin
 * @param change The state change that resizes the page
 */
export async function pinToAnchor(anchor: HTMLElement | null, change: () => void): Promise<void> {
  const port = findScrollParent(anchor);
  const beforeTop = anchor ? anchor.getBoundingClientRect().top : 0;
  change();
  if (!anchor || !port) return;
  await tick();
  const moved = anchor.getBoundingClientRect().top - beforeTop;
  if (moved !== 0) port.scrollTop += moved;
}

/**
 * Bring a scroll port to its end, once the update that grew it is on screen.
 *
 * A row appended by a click is written in the same tick the button was pressed,
 * so right after the call the port still measures the list without that row and
 * a scroll would stop one row short of the end. Waiting for the tick is what
 * makes the last row visible.
 *
 * @param element The scroll port, null when the list is not on screen
 */
export async function scrollToEnd(element: HTMLElement | null): Promise<void> {
  if (!element) return;
  await tick();
  element.scrollTop = element.scrollHeight;
}

/**
 * A reactive scroll helper that provides a fast smooth scroll implementation.
 *
 * @param element The HTML element to be scrolled.
 */
export function fastSmoothScroll(element: HTMLElement | null | (() => HTMLElement | null)) {
  let isScrolling = $state(false);
  // Track the pending animation frame so a new scrollTo() cancels the
  // unfinished animation. Without this, a quick nav switch while a smooth
  // scroll is still running leaves the viewport in a race between the two
  // animations, and the foundKey effect may read a stale scroll position.
  let animationFrame: number | null = null;

  function cancelAnimation() {
    if (animationFrame !== null) {
      cancelAnimationFrame(animationFrame);
      animationFrame = null;
    }
  }

  /**
   * Scroll to a target position.
   *
   * @param target Target scroll position.
   * @param duration Duration of the scroll animation in milliseconds. Defaults to 250.
   */
  function scrollTo(target: number, duration = 250, onScrollEnd?: () => void, tolerance = 10) {
    const el = typeof element === "function" ? element() : element;
    if (!el) return;

    // A new scroll request always supersedes the previous animation
    cancelAnimation();

    /**
     * Tolerance default to 10px
     * If the current scroll position is within 10px of the target position, it is considered aligned,
     * which can prevent micro-pixel deviations from causing repeated scrolling or UI jitter.
     * If triggered by scroll_trigger (i.e., manual click), the tolerance is ignored and scrolling is performed directly.
     */
    if (Math.abs(el.scrollTop - target) <= tolerance) {
      return;
    }

    if (duration <= 0) {
      // Mark as scrolling before the rAF callback actually moves the element,
      // so the foundKey effect in ArgCardList skips updating card_indicate
      // while the viewport is still at the old position
      isScrolling = true;
      animationFrame = requestAnimationFrame(() => {
        animationFrame = null;
        if (el) el.scrollTop = target;
        animationFrame = requestAnimationFrame(() => {
          animationFrame = null;
          isScrolling = false;
          onScrollEnd?.();
        });
      });
      return;
    }

    const start = el.scrollTop;
    const distance = target - start;
    let startTime: number | null = null;

    function animation(currentTime: number) {
      if (startTime === null) startTime = currentTime;
      const timeElapsed = currentTime - startTime;
      const progress = Math.min(timeElapsed / duration, 1);

      // easeOutQuad
      const ease = progress * (2 - progress);

      if (el) {
        el.scrollTop = start + distance * ease;
      }

      if (timeElapsed < duration) {
        animationFrame = requestAnimationFrame(animation);
      } else {
        animationFrame = requestAnimationFrame(() => {
          animationFrame = null;
          isScrolling = false;
          onScrollEnd?.();
        });
      }
    }

    isScrolling = true;
    animationFrame = requestAnimationFrame(animation);
  }

  return {
    get isScrolling() {
      return isScrolling;
    },
    scrollTo,
  };
}
