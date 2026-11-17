<script lang="ts">
  import { untrack } from "svelte";
  import TopStackNode from "./TopStackNode.svelte";
  import { TestTopStack, stackFixtureState } from "./stack-fixture.svelte";

  type Props = {
    /** Installs the stack context */
    root?: boolean;
    /** Layer pushed by this node */
    layer?: number;
    /** Precedence of this node's layer; nested nodes push 10 more */
    priority?: number;
    /** Number of nested nodes below this one, each contributing its parent's layer + 10 */
    depth?: number;
  };
  let { root = false, layer = -1, priority = 0, depth = 0 }: Props = $props();

  // Read once at mount: the layer identity is what the stack uses to remove the
  // entry when this node is destroyed.
  const handle = untrack(() => (root ? TestTopStack.init() : undefined));
  TestTopStack.use(
    untrack(() => layer),
    { priority: untrack(() => priority) },
  );
</script>

{#if handle}
  <span data-testid="top-value">{handle.value}</span>
{/if}

{#if depth > 0 && stackFixtureState.nestedVisible}
  <TopStackNode layer={layer + 10} priority={priority + 10} depth={depth - 1} />
{/if}
