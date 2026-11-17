<script lang="ts">
  import { onMount } from "svelte";
  import type { Component } from "svelte";
  import { page } from "$app/state";
  import TitleBar from "$lib/components/TitleBar.svelte";
  import { i18nState } from "$lib/i18n/state.svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { resetDragRegion } from "$lib/windowFrame.svelte";
  import "../app.css";

  let { children } = $props();

  const sharedState = useSharedState();

  // Dev-only route switcher (bottom-right corner). Loaded lazily through a
  // dynamic import guarded by import.meta.env.DEV: at build time the
  // condition is replaced with the literal false, so the branch — and the
  // module itself — is eliminated and never bundled into production builds.
  let DevRouteSwitcher: Component | undefined = $state();

  onMount(() => {
    if (!import.meta.env.DEV) return;
    import("$lib/components/DevRouteSwitcher.svelte")
      .then((mod) => {
        DevRouteSwitcher = mod.default;
      })
      .catch((err) => {
        console.error("Failed to load DevRouteSwitcher:", err);
      });
  });

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

  // The drag region belongs to the embedded page: leaving it (setup, loading,
  // error) drops the reported band and the title bar goes back to its own.
  $effect(() => {
    if (page.route.id !== "/app") {
      resetDragRegion();
    }
  });
</script>

<!-- The embedded web app (/app) provides its own header, so the title bar
     becomes a floating overlay (drag strip + window controls) there.
     The outer flex column reserves the title bar height in normal flow, so
     page containers sized with h-full never overflow the viewport (which
     would otherwise add scrollbars to pages that fit on one screen). -->
<div class="flex h-screen flex-col">
  <TitleBar floating={page.route.id === "/app"} />
  <main class="min-h-0 flex-1">
    {@render children()}
  </main>
</div>

<!-- Dev-only launcher in the bottom-right corner. The module is only loaded
     (and only bundled) in dev builds; see the dynamic import above. -->
{#if DevRouteSwitcher}
  <DevRouteSwitcher />
{/if}
