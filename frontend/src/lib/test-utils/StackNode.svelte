<script lang="ts">
  import { untrack } from "svelte";
  import StackNode from "./StackNode.svelte";
  import { TestStack, stackFixtureState } from "./stack-fixture.svelte";

  type Props = {
    /** Installs the stack context */
    root?: boolean;
    /** Layer pushed by this node */
    layer?: number;
    /** Number of nested nodes below this one, each contributing +10 */
    depth?: number;
  };
  let { root = false, layer, depth = 0 }: Props = $props();

  // A root installs the stack, every node may contribute a layer. Both props
  // are read once at mount on purpose: the layer identity is what the stack
  // uses to remove the entry when this node is destroyed.
  const handle = untrack(() => (root ? TestStack.init() : undefined));
  TestStack.use(untrack(() => layer));
</script>

{#if handle}
  <span data-testid="stack-value">{handle.value}</span>
{/if}

{#if depth > 0 && stackFixtureState.nestedVisible}
  <StackNode layer={(layer ?? 0) + 10} depth={depth - 1} />
{/if}
