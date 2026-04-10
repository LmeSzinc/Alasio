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
 * @param duration Duration of the scroll animation in milliseconds.
 */
export function fastSmoothScroll(element: HTMLElement | null | (() => HTMLElement | null), duration = 250) {
  let isScrolling = $state(false);

  function scrollTo(target: number) {
    const el = typeof element === "function" ? element() : element;
    if (!el) return;

    const start = el.scrollTop;
    const distance = target - start;
    let startTime: number | null = null;

    function animation(currentTime: number) {
      if (startTime === null) startTime = currentTime;
      const timeElapsed = currentTime - startTime;
      const progress = Math.min(timeElapsed / duration, 1);

      // easeOutQuad
      const ease = progress * (2 - progress);

      const currentEl = typeof element === "function" ? element() : element;
      if (currentEl) {
        currentEl.scrollTop = start + distance * ease;
      }

      if (timeElapsed < duration) {
        requestAnimationFrame(animation);
      } else {
        isScrolling = false;
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
