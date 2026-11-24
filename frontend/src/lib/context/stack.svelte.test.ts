/**
 * Tests for the layered stack context that backs both the header slot context
 * and the drag region context.
 *
 * The stack is what makes "a page overrides its layout, and svelte restores the
 * layout when the page is destroyed" work without watching the route, so the
 * cases here are the ones that guarantee it: mount order decides precedence,
 * and a destroyed layer takes only its own entry with it.
 */
import { flushSync, mount, unmount } from "svelte";
import { afterEach, describe, expect, it } from "vitest";
import PriorityStack from "$lib/test-utils/PriorityStack.svelte";
import SamePriorityStack from "$lib/test-utils/SamePriorityStack.svelte";
import StackNode from "$lib/test-utils/StackNode.svelte";
import SwapStack from "$lib/test-utils/SwapStack.svelte";
import TopStackNode from "$lib/test-utils/TopStackNode.svelte";
import { flushEffects } from "$lib/test-utils/flush-effects";
import { stackFixtureState, swapFixtureState } from "$lib/test-utils/stack-fixture.svelte";

let mounted: ReturnType<typeof mount> | undefined;
let target: HTMLElement | undefined;

function render(props: { root?: boolean; layer?: number; depth?: number }): void {
  target = document.createElement("div");
  document.body.appendChild(target);
  mounted = mount(StackNode, { target, props });
}

function renderTop(props: { root?: boolean; layer?: number; depth?: number }): void {
  target = document.createElement("div");
  document.body.appendChild(target);
  mounted = mount(TopStackNode, { target, props });
}

/** Folded value of the stack, as the root node renders it */
function value(): number | undefined {
  const text = target?.querySelector("[data-testid=stack-value]")?.textContent;
  return text === undefined ? undefined : Number(text);
}

/** Topmost layer of the stack, as the root node renders it */
function topValue(): number | undefined {
  const text = target?.querySelector("[data-testid=top-value]")?.textContent;
  return text === undefined ? undefined : Number(text);
}

function renderPriority(): void {
  target = document.createElement("div");
  document.body.appendChild(target);
  mounted = mount(PriorityStack, { target });
}

function renderSamePriority(): void {
  target = document.createElement("div");
  document.body.appendChild(target);
  mounted = mount(SamePriorityStack, { target });
}

/** Winner of the priority fixture */
function priorityValue(): number | undefined {
  const text = target?.querySelector("[data-testid=priority-value]")?.textContent;
  return text === undefined ? undefined : Number(text);
}

afterEach(() => {
  if (mounted) {
    unmount(mounted);
    mounted = undefined;
  }
  stackFixtureState.nestedVisible = true;
  swapFixtureState.oldPage = true;
  swapFixtureState.newPage = false;
  document.body.innerHTML = "";
  target = undefined;
});

describe("TestStackLayers", () => {
  it("folds the base value when no layer is pushed", async () => {
    render({ root: true });
    await flushEffects();

    expect(value()).toBe(0);
  });

  it("folds every layer of a nested tree", async () => {
    render({ root: true, layer: 1, depth: 2 });
    await flushEffects();

    // root 1 + child 11 + grandchild 21
    expect(value()).toBe(33);
  });

  it("keeps zero as a layer instead of dropping it", async () => {
    render({ root: true, layer: 0, depth: 1 });
    await flushEffects();

    // A falsy layer must still be pushed: the drag region declares `left: 0`.
    expect(value()).toBe(10);
  });

  it("drops only the layers of the destroyed components", async () => {
    render({ root: true, layer: 1, depth: 2 });
    await flushEffects();
    expect(value()).toBe(33);

    stackFixtureState.nestedVisible = false;
    await flushEffects();

    // Both nested nodes are gone; the root layer survives.
    expect(value()).toBe(1);
  });
});

describe("TestStackOrder", () => {
  // Precedence is the priority a layer declares, never the push order: svelte
  // flushes effects bottom-up and can create a component at any render, so
  // "the innermost layer is pushed last" is not something to rely on.
  it("lets the inner layer win through its priority", async () => {
    renderTop({ root: true, layer: 1, depth: 2 });
    await flushEffects();

    expect(topValue()).toBe(21);
  });

  it("falls back to the outer layer when the inner one is destroyed", async () => {
    renderTop({ root: true, layer: 1, depth: 1 });
    await flushEffects();
    expect(topValue()).toBe(11);

    stackFixtureState.nestedVisible = false;
    await flushEffects();

    expect(topValue()).toBe(1);
  });

  it("lets a higher priority win regardless of the push order", async () => {
    renderPriority();
    await flushEffects();

    // A layout-level layer is pushed after the page layer (the component that
    // owns it is created on a later render); the page must still win.
    expect(priorityValue()).toBe(70);
  });

  it("sees the layer after the flush that pushes it", async () => {
    // The layer is pushed from an effect, so a consumer that renders before
    // that flush sees the empty stack and has to re-render once it lands: a
    // layout whose nav is declared by a child branch must show it without a
    // reload. `-1` is the empty-stack value of this fixture.
    renderTop({ root: true, layer: 1, depth: 1 });

    expect(topValue()).toBe(-1);
    await flushEffects();

    expect(topValue()).toBe(11);
  });
});

describe("TestStackSamePriority", () => {
  it("keeps layers of equal priority in the order they were added", async () => {
    // Equal priority falls back to the order the layers were added in, which
    // the stack takes from the entries array itself rather than from a
    // sequence number. The fold reads the last layer, so a reversed or
    // otherwise unstable order would return the first one instead of the third.
    renderSamePriority();
    await flushEffects();

    expect(topValue()).toBe(3);
  });
});

describe("TestStackReplacingPage", () => {
  it("keeps the layer of the page that replaces a destroyed one", async () => {
    // The layout pushes one layer whose identity changes with the route, and
    // the router replaces the page in a single update: the teardown of the old
    // page and the push of the new one land in the same batch. The new layer
    // must survive, otherwise the shell falls back to its empty state on every
    // route that hands over from a page which declared a layer of its own.
    target = document.createElement("div");
    document.body.appendChild(target);
    mounted = mount(SwapStack, { target });
    await flushEffects();
    expect(topValue()).toBe(1);

    flushSync(() => {
      swapFixtureState.newPage = true;
    });
    await flushEffects();

    expect(topValue()).toBe(2);
  });
});
