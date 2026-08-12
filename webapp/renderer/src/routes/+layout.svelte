<script lang="ts">
  import { onMount } from 'svelte';
  import { useSharedState } from '$lib/useSharedState.svelte';
  import { i18nState } from '$lib/i18n/index.svelte';
  import TitleBar from '$lib/components/TitleBar.svelte';
  import '../app.css';

  const sharedState = useSharedState();

  onMount(() => {
    // Sync language with shared state
    i18nState.l = sharedState.language;
  });
</script>

<TitleBar />
<slot />
