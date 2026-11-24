<script lang="ts">
  import { untrack } from "svelte";
  import { TestTopStack } from "./stack-fixture.svelte";

  type Props = {
    /** Layer contributed for the lifetime of this component */
    layer: number;
  };
  let { layer }: Props = $props();

  // Read once at mount: the layer identity is what the stack uses to remove the
  // entry when this node is destroyed.
  TestTopStack.use(untrack(() => layer));
</script>
