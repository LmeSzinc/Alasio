<script lang="ts">
  import LatePusher from "./LatePusher.svelte";
  import { TestTopStack } from "./stack-fixture.svelte";

  // Mirrors the real tree: a layout installs the stack and declares a layer,
  // a page rendered below it declares a higher-priority one, and a sibling
  // branch pushes a layout-level layer later than both.
  const handle = TestTopStack.init();
  TestTopStack.use(1);
  TestTopStack.use(70, { priority: 10 });
</script>

<span data-testid="priority-value">{handle.value}</span>
<LatePusher />
