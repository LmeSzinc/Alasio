<script lang="ts">
  import { onMount } from "svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
  let iframe: HTMLIFrameElement | undefined = $state();

  // The embedded frontend origin. Downlink messages are only sent to this
  // origin, and uplink messages are only accepted from it.
  const frontendOrigin = $derived(`http://127.0.0.1:${sharedState.backendPort}`);

  // Send the current display values down to the embedded frontend.
  // Runs on mount (once the iframe element exists) and whenever the
  // display values change. Sending the same value again is harmless: the
  // frontend's setLang no-ops on identical values.
  $effect(() => {
    const frame = iframe;
    if (!frame?.contentWindow) return;
    const origin = frontendOrigin;
    frame.contentWindow.postMessage({ type: "alasio:lang", lang: sharedState.displayLang }, origin);
    frame.contentWindow.postMessage({ type: "alasio:theme", theme: sharedState.displayTheme }, origin);
  });

  // The frontend registers its message listeners when its scripts run, so
  // a message posted before load could be lost. Re-send once the iframe
  // finished loading to guarantee the first frame converges.
  function handleLoad() {
    const frame = iframe;
    if (!frame?.contentWindow) return;
    frame.contentWindow.postMessage({ type: "alasio:lang", lang: sharedState.displayLang }, frontendOrigin);
    frame.contentWindow.postMessage({ type: "alasio:theme", theme: sharedState.displayTheme }, frontendOrigin);
  }

  onMount(() => {
    // Listen for uplink messages from the embedded frontend. Strict
    // source/origin validation: only the iframe at the local backend
    // address may drive the host language/theme.
    const listener = (event: MessageEvent) => {
      const frame = iframe;
      if (!frame) return;
      if (event.source !== frame.contentWindow) return;
      if (event.origin !== `http://127.0.0.1:${sharedState.backendPort}`) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "alasio:lang" && typeof data.lang === "string") {
        window.electronAPI.setLanguage(data.lang);
      } else if (data.type === "alasio:theme" && typeof data.theme === "string") {
        window.electronAPI.setTheme(data.theme);
      }
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  });
</script>

<!-- Full-viewport iframe: the embedded web app provides its own header,
     the floating TitleBar overlay (drag strip + window controls) sits on top -->
<div class="flex h-screen flex-col">
  <iframe
    bind:this={iframe}
    onload={handleLoad}
    src="http://127.0.0.1:{sharedState.backendPort}/?embedded=electron"
    class="flex-1 w-full border-0"
    title="Alasio App"
  ></iframe>
</div>
