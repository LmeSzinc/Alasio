import { getContext, setContext, type Snippet } from "svelte";

// We can render content using `{@render children()}`, but impossibe to render another snippet from child page like `{@render children.nav()}`.
// So here comes the magic to store the nav snippet in global context.
// https://github.com/sveltejs/kit/issues/12928#issuecomment-2627360639

/**
 * Creates a reusable slot context that can store and manage snippets across component hierarchy.
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
  const key = Symbol(name);

  // Use a relaxed structural type for the snippet to resolve "Two different types with this name exist"
  // errors while still ensuring that it is a function that returns a snippet-like value.
  type GenericSnippet = (...args: any[]) => { "{@render ...} must be called with a Snippet": any };

  interface ContextValue {
    snippet: GenericSnippet | undefined;
  }

  function init() {
    const context: ContextValue = $state({ snippet: undefined });
    return setContext(key, context);
  }

  function set(snippet: GenericSnippet) {
    const context = getContext<ContextValue>(key);
    if (!context) {
      return;
    }
    context.snippet = snippet;
  }

  function getContextValue() {
    return getContext<ContextValue>(key);
  }

  function clean(snippet?: GenericSnippet) {
    const context = getContextValue();
    if (!context) {
      return;
    }
    if (snippet === undefined || context.snippet === snippet) {
      context.snippet = undefined;
    }
  }

  /**
   * A reactive utility to manage the snippet for the component's lifecycle.
   * It automatically sets the snippet when the component mounts (or when the snippet changes)
   * and cleans it up when the component is destroyed (or when the snippet changes).
   *
   * @param snippet The snippet to be set in the context. Can be a reactive value.
   *                If the snippet becomes undefined, it will be cleaned from the context.
   */
  function use(snippet: GenericSnippet | undefined) {
    $effect(() => {
      if (snippet) {
        set(snippet);
        return () => {
          clean(snippet);
        };
      }
    });
  }

  return {
    init,
    set,
    clean,
    use,
    /**
     * Direct access to the current snippet stored in the context.
     * Returns undefined if the context hasn't been initialized or no snippet is set.
     */
    get snippet(): Snippet | undefined {
      // Internal state is cast to Snippet for external consumption
      return getContextValue()?.snippet as Snippet | undefined;
    },
  };
}

export const NavContext = createSlotContext("nav_slot");
export const HeaderContext = createSlotContext("header_slot");
