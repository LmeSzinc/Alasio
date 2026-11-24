/**
 * A Svelte Action that calls a callback once, when the element first comes into
 * the visible part of its scroll container (or of the viewport).
 *
 * A long editor mounts its rows as plain text and turns each of them into its
 * real editor only when the row is on screen: the editor of one row is
 * expensive (one dropdown per level of the filter), while a filter may hold a
 * hundred rows and the dialog shows a dozen. The row keeps the editor once it
 * was shown, so scrolling back does not rebuild it.
 *
 * Without an IntersectionObserver the callback runs right away, so a row is
 * never stuck as plain text (`rootMargin` then does nothing).
 *
 * example:
 * <div use:visibleOnce={() => (hydrated = true)}></div>
 */
export function visibleOnce(node: HTMLElement, onVisible: () => void) {
  if (typeof IntersectionObserver === "undefined") {
    onVisible();
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      // The observer only lives until the first sighting, a row that scrolled
      // away again before this callback still counts as seen
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      onVisible();
    },
    {
      // A row is prepared a little before it scrolls in, so it is already its
      // real editor when it appears at the edge of the list
      rootMargin: "100px 0px",
    },
  );
  observer.observe(node);

  return {
    /**
     * This `destroy` function is called when the element is removed from the DOM.
     */
    destroy() {
      observer.disconnect();
    },
  };
}
