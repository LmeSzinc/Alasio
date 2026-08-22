<script lang="ts">
  import { page } from "$app/state";
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { i18nState } from "$lib/i18n/state.svelte";
  import TitleBar from "$lib/components/TitleBar.svelte";
  import "../app.css";

  const sharedState = useSharedState();

  // Keep the renderer i18n state in sync with the host's display language.
  // The host (main process AppState) is the single source of truth.
  $effect(() => {
    i18nState.l = sharedState.displayLang;
  });

  // Keep the renderer theme in sync with the host's display theme: toggling
  // the .dark class on <html> switches the Tailwind design tokens, and the
  // colorScheme style keeps native controls (scrollbars, form controls) in
  // the same theme. This covers every renderer page (loading/setup/error/
  // app shell) and the title bar; the embedded frontend iframe applies the
  // theme itself via mode-watcher. The host (main process AppState) is the
  // single source of truth.
  $effect(() => {
    const dark = sharedState.displayTheme === "dark";
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  });
</script>

<!-- The embedded web app (/app) provides its own header, so the title bar
     becomes a floating overlay (drag strip + window controls) there. -->
<TitleBar floating={page.route.id === "/app"} />
<slot />
