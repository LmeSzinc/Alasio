<script lang="ts">
  import { untrack } from "svelte";
  import { TestTopStack } from "./stack-fixture.svelte";

  // Installs the stack and pushes three layers of the same priority from one
  // component. The order of these calls is the order the effects run in, so it
  // is also the order the layers are added in, whatever svelte's flush order
  // across components is.
  const handle = untrack(() => TestTopStack.init());
  TestTopStack.use(untrack(() => 1));
  TestTopStack.use(untrack(() => 2));
  TestTopStack.use(untrack(() => 3));
</script>

<span data-testid="top-value">{handle.value}</span>
