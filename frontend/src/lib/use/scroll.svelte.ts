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
 * A reactive scroll helper that provides a fast smooth scroll implementation.
 *
 * @param element The HTML element to be scrolled.
 */
export function fastSmoothScroll(element: HTMLElement | null | (() => HTMLElement | null)) {
  let isScrolling = $state(false);

  /**
   * Scroll to a target position.
   *
   * @param target Target scroll position.
   * @param duration Duration of the scroll animation in milliseconds. Defaults to 250.
   */
  function scrollTo(target: number, duration = 250, onScrollEnd?: () => void, tolerance = 10) {
    const el = typeof element === "function" ? element() : element;
    if (!el) return;

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
      requestAnimationFrame(() => {
        if (el) el.scrollTop = target;
        requestAnimationFrame(() => {
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
        requestAnimationFrame(animation);
      } else {
        requestAnimationFrame(() => {
          isScrolling = false;
          onScrollEnd?.();
        });
      }
    }

    isScrolling = true;
    requestAnimationFrame(animation);
  }

  return {
    get isScrolling() {
      return isScrolling;
    },
    scrollTo,
  };
}
