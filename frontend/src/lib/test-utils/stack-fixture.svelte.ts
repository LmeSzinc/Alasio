import { createStackContext } from "$lib/context/stack.svelte";

/**
 * Stack contexts used by the `stack.svelte.test.ts` fixtures.
 *
 * `TestStack` folds the sum of the layers, so the test can tell which layers
 * are alive. `TestTopStack` folds the last layer, so the test can tell the
 * order: the layer pushed latest wins, which must be the one of the innermost
 * component.
 */
export const TestStack = createStackContext<number, number>("test_stack", (layers) =>
  layers.reduce((total, layer) => total + layer, 0),
);

export const TestTopStack = createStackContext<number, number>("test_top_stack", (layers) =>
  layers.length > 0 ? layers[layers.length - 1] : -1,
);

/** Lets a test unmount the nested layer without unmounting the root */
export const stackFixtureState = $state({ nestedVisible: true });

/**
 * Which pages `SwapStack` shows.
 *
 * `newPage` is switched on by the test; the fixture then drops `oldPage` in a
 * nested flush, so the old page's teardown runs after the new page pushed its
 * layer and reads it as a write of the running batch.
 */
export const swapFixtureState = $state({ oldPage: true, newPage: false });
