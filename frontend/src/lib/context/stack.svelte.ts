import { getContext, setContext, untrack } from "svelte";

/**
 * A reactive stack of layers shared through svelte context.
 *
 * A component installs the stack with `init()`, descendants push layers with
 * `use()`, and each layer is dropped automatically when the component that
 * pushed it is destroyed.
 *
 * Precedence is explicit, not mount ordering: `{@render}` and conditional
 * blocks make mount order unreliable (a component rendered from a snippet can
 * be created after its own descendants, and a later re-render can create one
 * at any time). Callers therefore state which layer wins, and layers of equal
 * priority keep the push order.
 *
 * This is the mechanism behind both the header slot context and the drag
 * region context: a page overrides what its layout declared, and svelte's own
 * lifecycle restores the layout value when the page is gone.
 */

/** Reduces the layers of one stack to the value its consumers read */
export type StackFold<L, T> = (layers: readonly L[]) => T;

/** Precedence of a layer; a higher value wins over a lower one */
export interface LayerOptions {
  priority?: number;
}

/**
 * Priority of a layer declared by a page, above the `LAYOUT_LAYER` of the
 * layouts it is rendered in. A page narrows what its layout declared instead
 * of replacing it, so the two never compete for the same insets.
 */
export const PAGE_LAYER = 10;

/** Priority of a layer declared by a layout (the default) */
export const LAYOUT_LAYER = 0;

/** Read-only view of a stack, handed to the component that called `init()` */
export interface StackHandle<T> {
  /** Folded value of the stack (reactive) */
  readonly value: T;
  /** Number of layers in the stack (reactive) */
  readonly size: number;
}

interface StackEntry<L> {
  value: L;
  priority: number;
}

/** What consumers read: the folded layers and how many there are */
interface StackSnapshot<T> {
  value: T;
  size: number;
}

/**
 * State of one installed stack.
 *
 * The entries are a plain array and `snapshot` is the only reactive value; it
 * is recomputed from the entries whenever they change. This is not an
 * optimisation. An effect teardown reads reactive state as it was before the
 * batch being flushed (svelte reverts the writes that caused the teardown, see
 * `teardown_view` in its runtime), and `untrack` does not change that: it turns
 * tracking off, the value read is the same one. A teardown that rebuilt the
 * entries from a reactive read therefore dropped a layer pushed earlier in the
 * same flush — the route hand-over, where the page being left removes its layer
 * while the page being entered declares its own, left the shell without a layer
 * at all. Mutating a plain array and publishing the result cannot read such a
 * stale value.
 */
interface ContextValue<L, T> {
  /** Plain, never proxied: see the note on this interface */
  entries: StackEntry<L>[];
  /** Folded entries, reactive: reading it subscribes the caller */
  readonly snapshot: StackSnapshot<T>;
  /** Recomputes `snapshot` from `entries` */
  publish(): void;
}

/**
 * Creates an independent stack context.
 *
 * Args:
 *   name: Unique name, used as the context key
 *   fold: Reduces the layers to the value the consumers read; it receives them
 *     from the lowest priority to the highest, so the last one is the winner.
 *     It runs once per change of the stack, so it has to be cheap and free of
 *     side effects
 */
export function createStackContext<L, T>(name: string, fold: StackFold<L, T>) {
  const key = Symbol(name);

  // `sort` is stable (the ES2019 requirement), so layers of equal priority keep
  // the order they were added in: the array itself is the push order, and no
  // sequence number has to be handed out and kept in sync with it.
  function ordered(entries: readonly StackEntry<L>[]): L[] {
    return [...entries].sort((a, b) => a.priority - b.priority).map((entry) => entry.value);
  }

  /**
   * Installs the stack on the calling component and returns a handle to it.
   *
   * Must be called during component initialisation. The handle is meant for
   * code that runs outside the component tree (an effect started in
   * `onMount`), where `getContext` is not available anymore.
   */
  function init(): StackHandle<T> {
    const entries: StackEntry<L>[] = [];
    // Raw: the folded value is whatever `fold` returns (a snippet, a region)
    // and must not be proxied, and the stack compares it by identity anyway
    let snapshot = $state.raw<StackSnapshot<T>>({ value: fold([]), size: 0 });
    const context: ContextValue<L, T> = {
      entries,
      get snapshot() {
        return snapshot;
      },
      publish() {
        snapshot = { value: fold(ordered(entries)), size: entries.length };
      },
    };
    setContext(key, context);
    return {
      get value() {
        return context.snapshot.value;
      },
      get size() {
        return context.snapshot.size;
      },
    };
  }

  function current(): ContextValue<L, T> | undefined {
    return getContext<ContextValue<L, T>>(key);
  }

  /** Appends a layer that stays until the component that added it is destroyed */
  function add(context: ContextValue<L, T>, layer: L, priority: number) {
    context.entries.push({ value: layer, priority });
    context.publish();
  }

  /** Drops the most recent entry of `layer`, in place: the array is shared */
  function drop(context: ContextValue<L, T>, layer: L) {
    for (let index = context.entries.length - 1; index >= 0; index -= 1) {
      if (context.entries[index].value !== layer) continue;
      context.entries.splice(index, 1);
      context.publish();
      return;
    }
  }

  /** Appends a layer that stays until `clean()` removes it */
  function push(layer: L, options?: LayerOptions) {
    const context = current();
    if (!context) return;
    add(context, layer, options?.priority ?? LAYOUT_LAYER);
  }

  /**
   * Appends a layer for the lifetime of the calling component.
   *
   * Must be called during component initialisation. The context is captured
   * here on purpose: the teardown runs while the component is being destroyed,
   * where `getContext` is no longer allowed and would throw
   * lifecycle_outside_component.
   *
   * The push happens inside the effect, not synchronously: a layer is pushed
   * while the parent that renders its consumers is already evaluating, and a
   * write to the state it just read would not re-render those consumers (a nav
   * declared by a layout stayed missing until a reload). The price is that the
   * flush order is svelte's, not the component tree's, which is why precedence
   * is `priority`, not push order.
   *
   * Args:
   *   layer: The layer, or `undefined` to contribute nothing
   *   options: Precedence of the layer, `LAYOUT_LAYER` when omitted
   */
  function use(layer: L | undefined, options?: LayerOptions) {
    const context = current();
    if (!context || layer === undefined) return;
    const priority = options?.priority ?? LAYOUT_LAYER;

    $effect(() => {
      // Untracked: pushing must not make this effect depend on what it edits
      untrack(() => add(context, layer, priority));
      return () => {
        // Untracked for the same reason, and `getContext` is already captured.
        // The teardown only edits the plain entries: a reactive read here would
        // see the stack as it was before this flush and take the layers of the
        // components mounted alongside this one with it.
        untrack(() => drop(context, layer));
      };
    });
  }

  /**
   * Removes one layer, or every layer when `layer` is omitted.
   *
   * Args:
   *   layer: The layer to remove, `undefined` to clear the whole stack
   */
  function clean(layer?: L) {
    const context = current();
    if (!context) return;
    if (layer === undefined) {
      context.entries.length = 0;
    } else {
      for (let index = context.entries.length - 1; index >= 0; index -= 1) {
        if (context.entries[index].value === layer) context.entries.splice(index, 1);
      }
    }
    context.publish();
  }

  /** Folded value of the nearest stack, `undefined` when there is none */
  function read(): T | undefined {
    const context = current();
    if (!context) return undefined;
    return context.snapshot.value;
  }

  return { init, push, use, clean, read };
}
