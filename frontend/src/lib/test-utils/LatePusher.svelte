<script lang="ts">
  import { TestTopStack } from "./stack-fixture.svelte";

  // Simulates a component that comes into existence after the ones that matter
  // (in the app: a nav rendered from a snippet of a sibling branch, created on
  // a later re-render). It pushes a layout-level layer, so it lands after the
  // page layer in push order; priority -- not push order -- decides the winner.
  TestTopStack.use(7);
</script>
