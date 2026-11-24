<script lang="ts">
  import { flushSync, untrack } from "svelte";
  import SwapLayer from "./SwapLayer.svelte";
  import { TestTopStack, swapFixtureState } from "./stack-fixture.svelte";

  // Installs the stack once; the layer above it belongs to the page currently
  // shown, and the two pages are swapped from the outside by the test.
  const handle = untrack(() => TestTopStack.init());

  // The new page is shown first and the old one is dropped in a nested flush of
  // the same batch — the way the router hands over from one page to the next.
  // The nested flush is what makes svelte treat the new page's push as a write
  // of the running batch, so the old page's teardown reads it as reverted. The
  // write is untracked so this effect only follows the new page.
  $effect(() => {
    if (swapFixtureState.newPage) {
      untrack(() => {
        flushSync(() => {
          swapFixtureState.oldPage = false;
        });
      });
    }
  });
</script>

<span data-testid="top-value">{handle.value}</span>

{#if swapFixtureState.oldPage}
  <SwapLayer layer={1} />
{/if}

{#if swapFixtureState.newPage}
  <SwapLayer layer={2} />
{/if}
