/**
 * Defines the shape of the state object that this action expects.
 * It must be a mutable object with `width` and `height` properties.
 */
type SizeState = {
  width: number;
  height: number;
};

/**
 * A Svelte Action that observes an element's size and directly mutates
 * a provided Svelte 5 state object with the new width and height.
 *
 * @param node The HTML element the action is applied to.
 * @param stateTarget The Svelte 5 state object (e.g., from `$state`) to be updated.
 *
 * example:
 * let size = $state({ width: 0, height: 0 });
 * <div use:elementSize={size}></div>
 */
export function elementSize(node: HTMLElement, stateTarget: SizeState) {
  // Keep a reference to the current state target. This allows the `update` hook
  // to swap out the target if the action's parameters change.
  let currentTarget = stateTarget;

  const observer = new ResizeObserver((entries) => {
    // Only proceed if a valid state target has been provided.
    if (currentTarget) {
      // We are only observing one element.
      if (entries[0]) {
        const { width, height } = entries[0].contentRect;

        // The core logic: directly mutate the properties of the provided state object.
        // Svelte 5's reactivity system will automatically detect these mutations
        // and trigger the necessary UI updates.
        currentTarget.width = width;
        currentTarget.height = height;
      }
    }
  });

  // Start observing the element as soon as the action is mounted.
  observer.observe(node);

  return {
    /**
     * This `update` function is called when the parameters of the `use:` directive change.
     * For example, if the user were to do <div use:elementSize={anotherState} />
     */
    update(newStateTarget: SizeState) {
      // Update the reference to the state object we are mutating.
      currentTarget = newStateTarget;
    },
    /**
     * This `destroy` function is called when the element is removed from the DOM.
     */
    destroy() {
      // Stop observing the element to prevent memory leaks.
      observer.disconnect();
    },
  };
}
export type ViewportSizeParams = SizeState | { state: SizeState; root?: HTMLElement | null };

/**
 * A Svelte Action that observes an element's visible size in its scroll container
 * (or viewport) and directly mutates a provided Svelte 5 state object.
 *
 * @param node The HTML element the action is applied to.
 * @param params The Svelte 5 state object or an options object with state and root.
 */
export function elementViewportSize(node: HTMLElement, params: ViewportSizeParams) {
  let currentTarget = "state" in params ? params.state : params;
  let root = "root" in params ? params.root : null;
  let observer: IntersectionObserver | null = null;

  function setupObserver() {
    if (observer) observer.disconnect();
    observer = new IntersectionObserver(
      (entries) => {
        if (currentTarget && entries[0]) {
          const { width, height } = entries[0].intersectionRect;
          currentTarget.width = width;
          currentTarget.height = height;
        }
      },
      {
        root,
        // Use many thresholds to track size changes while scrolling
        threshold: Array.from({ length: 101 }, (_, i) => i / 100),
      },
    );
    observer.observe(node);
  }

  setupObserver();

  return {
    update(newParams: ViewportSizeParams) {
      currentTarget = "state" in newParams ? newParams.state : newParams;
      const newRoot = "root" in newParams ? newParams.root : null;
      if (newRoot !== root) {
        root = newRoot;
        setupObserver();
      }
    },
    destroy() {
      if (observer) observer.disconnect();
    },
  };
}
