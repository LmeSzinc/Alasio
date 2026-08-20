<script lang="ts">
  import { page } from '$app/state';
  import { useSharedState } from '$lib/useSharedState.svelte';
  import { i18nState } from '$lib/i18n/state.svelte';
  import TitleBar from '$lib/components/TitleBar.svelte';
  import '../app.css';

  const sharedState = useSharedState();

  // Keep the renderer i18n state in sync with the host's display language.
  // The host (main process AppState) is the single source of truth.
  $effect(() => {
    i18nState.l = sharedState.displayLang;
  });
</script>

<!-- The embedded web app (/app) provides its own header, so the title bar
     becomes a floating overlay (drag strip + window controls) there. -->
<TitleBar floating={page.route.id === '/app'} />
<slot />
