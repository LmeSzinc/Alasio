<script lang="ts">
  import DevNav from "./DevNav.svelte";
  import { useNavContext } from "$lib/navcontext.svelte";

  let { children } = $props();

  // nav context
  useNavContext(nav);
</script>

{#snippet nav()}
  <DevNav />
{/snippet}

{@render children()}
