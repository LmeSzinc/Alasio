<script lang="ts">
  import { useNavContext } from "$lib/navcontext.svelte";
  import { useTopic } from "$lib/ws";
  import { onDestroy } from "svelte";
  import ConfigArg from "./ConfigArg.svelte";
  import ConfigNav from "./ConfigNav.svelte";

  // nav context
  useNavContext(nav);

  let { data } = $props();
  const config_name = $derived(data.config_name);

  // topic
  const stateClient = useTopic("ConnState");
  const configRpc = stateClient.resilientRpc();
  const configResetRpc = stateClient.rpc();
  const navRpc = stateClient.resilientRpc();

  // shared state between 2 nav instances
  let nav_name = $state("");
  let card_name = $state("");
  let opened_nav = $state("");

  // Effect to call RPC when config_name changes.
  $effect(() => {
    if (config_name) {
      configRpc.call("set_config", { name: config_name });
      nav_name = "";
      card_name = "";
      opened_nav = "";
    }
  });
  // Clear nav state on page leave
  onDestroy(() => {
    configResetRpc.call("set_config", { name: "" });
  });

  function onCardClick(nav: string, card: string) {
    navRpc.call("set_nav", { name: nav });
    nav_name = nav;
    card_name = card;
  }
</script>

{#snippet nav()}
  <ConfigNav {nav_name} {card_name} bind:opened_nav {onCardClick} />
{/snippet}

<ConfigArg indicateCard={card_name} />
