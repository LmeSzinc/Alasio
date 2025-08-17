<script lang="ts">
  import { useNavContext } from "$lib/navcontext.svelte";
  import ConfigNav from "./ConfigNav.svelte";

  // nav context
  useNavContext(nav);

  let { data } = $props();
  let config_name = $derived(data.config_name);
</script>

{#snippet nav()}
  <ConfigNav {config_name} />
{/snippet}
