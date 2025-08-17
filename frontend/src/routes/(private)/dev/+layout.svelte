<script lang="ts">
  import { MainNav } from "$lib/components/nav";
  import { useNavContext } from "$lib/navcontext.svelte";

  let { children } = $props();

  // nav context
  useNavContext(nav);
</script>

{#snippet nav()}
  <MainNav />
{/snippet}

{@render children()}
