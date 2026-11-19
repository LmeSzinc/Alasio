<script lang="ts">
  import { page } from "$app/state";
  import { devNavTitle } from "$lib/dev/nav";
  import { HeaderContext, NavContext } from "$lib/slotcontext.svelte";
  import DevNav from "./DevNav.svelte";

  let { children } = $props();

  // nav context
  NavContext.use(nav);
  // The header of the dev tools is declared here, not in DevNav: a compact page
  // renders no nav column, so DevNav would not exist to provide a title and the
  // header would fall back to the placeholder. Compact pages still override it
  // with their own snippet at PAGE_LAYER, which carries the mod selector.
  HeaderContext.use(header);

  const title = $derived(devNavTitle(page.url.pathname));
</script>

{#snippet nav()}
  <DevNav />
{/snippet}

{#snippet header()}
  <h1 class="w-full min-w-0 flex-1 overflow-hidden text-center text-lg whitespace-nowrap">{title}</h1>
{/snippet}

{@render children()}
