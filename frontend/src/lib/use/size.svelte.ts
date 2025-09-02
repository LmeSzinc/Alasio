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
 */
export function sizeObserver(node: HTMLElement, stateTarget: SizeState | undefined | null) {
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
     * For example, if the user were to do <div use:sizeObserver={anotherState} />
     */
    update(newStateTarget: SizeState | undefined | null) {
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
