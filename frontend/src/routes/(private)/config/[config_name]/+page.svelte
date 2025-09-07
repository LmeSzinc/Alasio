<script lang="ts">
  import { useNavContext } from "$lib/navcontext.svelte";
  import ConfigArg from "./ConfigArg.svelte";
  import ConfigNav from "./ConfigNav.svelte";

  // nav context
  useNavContext(nav);

  let { data } = $props();
  const config_name = $derived(data.config_name);

  // share card_name between ConfigNav and ConfigArg
  // when user clicks the card of ConfigNav, ConfigArg will scroll to it
  let card_name = $state("");
  function onCardClick(nav: string, card: string) {
    card_name = card;
  }
</script>

{#snippet nav()}
  <ConfigNav {config_name} {onCardClick} />
{/snippet}

<ConfigArg indicateCard={card_name} />
