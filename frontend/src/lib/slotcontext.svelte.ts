import type { Snippet } from "svelte";
import { createStackContext } from "$lib/context/stack.svelte";

// We can render content using `{@render children()}`, but impossibe to render another snippet from child page like `{@render children.nav()}`.
// So here comes the magic to store the nav snippet in global context.
// https://github.com/sveltejs/kit/issues/12928#issuecomment-2627360639

/**
 * Creates a reusable slot context that can manage snippets across component hierarchy
 * using a stack model. Child components can push their own snippet, and when they
 * unmount, the parent's snippet is automatically restored.
 *
 * The stack itself is `createStackContext` (see `$lib/context/stack.svelte`); a
 * slot context is the specialization whose folded value is the topmost layer.
 * The same mechanism backs the drag region context, so both share one tested
 * push / teardown implementation.
 *
 * @param name A unique identifier for this context (used to create the context key)
 * @returns An object with methods and a reactive snippet property
 *
 * @example
 * ```ts
 * // Create multiple independent contexts
 * const NavContext = createSlotContext("nav_slot");
 * const HeaderContext = createSlotContext("header_slot");
 *
 * // In parent component
 * NavContext.init();
 *
 * // In child component
 * NavContext.set(myNavSnippet);
 * // or use the reactive utility
 * NavContext.use(myNavSnippet);
 *
 * // Direct access to snippet
 * {#if NavContext.snippet}
 *   {@render NavContext.snippet()}
 * {/if}
 * ```
 */
export function createSlotContext(name: string) {
  // Use a relaxed structural type for the snippet to resolve "Two different types with this name exist"
  // errors while still ensuring that it is a function that returns a snippet-like value.
  type GenericSnippet = (...args: any[]) => { "{@render ...} must be called with a Snippet": any };

  const stack = createStackContext<GenericSnippet, GenericSnippet | undefined>(name, (layers) =>
    layers.length > 0 ? layers[layers.length - 1] : undefined,
  );

  return {
    init: stack.init,
    set: stack.push,
    clean: stack.clean,
    use: stack.use,
    /**
     * Direct access to the current snippet stored in the context.
     * Returns the top-most snippet on the stack (the most recently set by a descendant),
     * or undefined if the context hasn't been initialized or the stack is empty.
     */
    get snippet(): Snippet | undefined {
      // Internal state is cast to Snippet for external consumption
      return stack.read() as Snippet | undefined;
    },
  };
}

export const NavContext = createSlotContext("nav_slot");
export const HeaderContext = createSlotContext("header_slot");
