<script lang="ts">
  import { MainNav } from "$lib/components/nav";
  import { setNavContext, cleanNavContext } from "$lib/navcontext.svelte";

  let { children } = $props();

  // nav context
  setNavContext(nav);
  $effect(() => {
    return cleanNavContext;
  });
</script>

{#snippet nav()}
  <MainNav />
{/snippet}

{@render children()}
