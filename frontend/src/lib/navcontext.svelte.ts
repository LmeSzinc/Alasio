import { getContext, setContext, type Snippet } from "svelte";

// We can render content using `{@render children()}`, but impossibe to render another snippet from child page like `{@render children.nav()}`.
// So here comes the magic to store the nav snippet in global context.
// https://github.com/sveltejs/kit/issues/12928#issuecomment-2627360639

const key = Symbol("nav-slot");

interface SlotContext {
  nav: Snippet | undefined;
}

export function initNavContext() {
  const slots: SlotContext = $state({ nav: undefined });
  return setContext(key, slots);
}

export function setNavContext(nav: Snippet) {
  const context = getContext<SlotContext>(key);
  if (!context) {
    return;
  }
  context.nav = nav;
}

export function cleanNavContext(nav?: Snippet) {
  const context = getContext<SlotContext>(key);
  if (!context) {
    return;
  }
  if (nav === undefined || context.nav === nav) {
    context.nav = undefined;
  }
}
/**
 * A reactive utility to manage the navigation snippet for the component's lifecycle.
 * It automatically sets the nav snippet when the component mounts (or when the snippet changes)
 * and cleans it up when the component is destroyed (or when the snippet changes).
 *
 * @param nav The nav snippet to be set in the context. Can be a reactive value.
 *            If the snippet becomes undefined, it will be cleaned from the context.
 */
export function useNavContext(nav: Snippet | undefined) {
  $effect(() => {
    if (nav) {
      setNavContext(nav);
      return () => {
        cleanNavContext(nav);
      };
    }
  });
}
