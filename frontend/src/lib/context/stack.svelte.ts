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
  /** Push order, keeps layers of equal priority in the order they were added */
  seq: number;
}

interface ContextValue<L> {
  entries: StackEntry<L>[];
  nextSeq: number;
}

/**
 * Creates an independent stack context.
 *
 * Args:
 *   name: Unique name, used as the context key
 *   fold: Reduces the layers to the value the consumers read; it receives them
 *     from the lowest priority to the highest, so the last one is the winner
 */
export function createStackContext<L, T>(name: string, fold: StackFold<L, T>) {
  const key = Symbol(name);

  function ordered(entries: readonly StackEntry<L>[]): L[] {
    return [...entries].sort((a, b) => a.priority - b.priority || a.seq - b.seq).map((entry) => entry.value);
  }

  /**
   * Installs the stack on the calling component and returns a handle to it.
   *
   * Must be called during component initialisation. The handle is meant for
   * code that runs outside the component tree (an effect started in
   * `onMount`), where `getContext` is not available anymore.
   */
  function init(): StackHandle<T> {
    const context: ContextValue<L> = $state({ entries: [], nextSeq: 0 });
    setContext(key, context);
    return {
      get value() {
        return fold(ordered(context.entries));
      },
      get size() {
        return context.entries.length;
      },
    };
  }

  function current(): ContextValue<L> | undefined {
    return getContext<ContextValue<L>>(key);
  }

  /** Appends a layer that stays until `clean()` removes it */
  function push(layer: L, options?: LayerOptions) {
    const context = current();
    if (!context) return;
    context.entries = [
      ...context.entries,
      { value: layer, priority: options?.priority ?? LAYOUT_LAYER, seq: context.nextSeq },
    ];
    context.nextSeq += 1;
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
      untrack(() => {
        context.entries = [...context.entries, { value: layer, priority, seq: context.nextSeq }];
        context.nextSeq += 1;
      });
      return () => {
        // Untracked for the same reason, and `getContext` is already captured
        untrack(() => {
          context.entries = context.entries.filter((entry) => entry.value !== layer);
        });
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
    context.entries = layer === undefined ? [] : context.entries.filter((entry) => entry.value !== layer);
  }

  /** Folded value of the nearest stack, `undefined` when there is none */
  function read(): T | undefined {
    const context = current();
    return context ? fold(ordered(context.entries)) : undefined;
  }

  return { init, push, use, clean, read };
}
