<script lang="ts">
  import { useNavContext } from "$lib/navcontext.svelte";
  import ConfigArg from "./ConfigArg.svelte";
  import ConfigNav from "./ConfigNav.svelte";

  // nav context
  useNavContext(nav);

  let { data } = $props();
  const config_name = $derived(data.config_name);
</script>

{#snippet nav()}
  <ConfigNav {config_name} />
{/snippet}

<ConfigArg />
