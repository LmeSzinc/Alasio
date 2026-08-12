<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
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

<!-- The embedded web app (/app) provides its own header, so the title bar
     becomes a floating overlay (drag strip + window controls) there. -->
<TitleBar floating={page.route.id === '/app'} />
<slot />
