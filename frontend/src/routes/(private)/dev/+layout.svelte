<script lang="ts">
  import { NavContext } from "$lib/slotcontext.svelte";
  import DevNav from "./DevNav.svelte";

  let { children } = $props();

  // nav context
  NavContext.use(nav);
</script>

{#snippet nav()}
  <DevNav />
{/snippet}

{@render children()}
