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
  Object.assign(context, { nav });
}

export function cleanNavContext() {
  const context = getContext<SlotContext>(key);
  Object.assign(context, { nav: undefined });
}
