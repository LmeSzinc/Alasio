<script lang="ts">
  import { onMount } from "svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { i18nState } from "$lib/i18n/index.svelte";
  import Setup from "./routes/Setup.svelte";
  import Loading from "./routes/Loading.svelte";
  import AppPage from "./routes/AppPage.svelte";
  import Error from "./routes/Error.svelte";
  import TitleBar from "$lib/components/TitleBar.svelte";

  const sharedState = useSharedState();

  onMount(() => {
    // Sync language with shared state
    i18nState.l = sharedState.language;
  });
</script>

<TitleBar />
{#if sharedState.route === "setup"}
  <Setup />
{:else if sharedState.route === "loading"}
  <Loading />
{:else if sharedState.route === "app"}
  <AppPage />
{:else if sharedState.route === "error"}
  <Error />
{/if}
