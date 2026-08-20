<script lang="ts">
  import { onMount } from "svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
  let iframe: HTMLIFrameElement | undefined = $state();

  // The embedded frontend origin. Downlink messages are only sent to this
  // origin, and uplink messages are only accepted from it.
  const frontendOrigin = $derived(`http://127.0.0.1:${sharedState.backendPort}`);

  // Whether the iframe has finished loading the backend page. Until the
  // load event the iframe is still on its initial about:blank document,
  // whose origin is inherited from the parent (app://bundle); posting
  // with a strict targetOrigin then throws "target origin does not match
  // the recipient window's origin" instead of delivering. Downlink
  // messages are only sent after the backend page is actually loaded.
  let iframeLoaded = false;

  // Send the current display values down to the embedded frontend.
  // Sending the same value again is harmless: the frontend's setLang
  // no-ops on identical values.
  function sendDownlink() {
    const frame = iframe;
    if (!frame?.contentWindow) return;
    const origin = frontendOrigin;
    frame.contentWindow.postMessage({ type: "alasio:lang", lang: sharedState.displayLang }, origin);
    frame.contentWindow.postMessage({ type: "alasio:theme", theme: sharedState.displayTheme }, origin);
  }

  // Send whenever the display values change after the iframe finished
  // loading (the load handler below sends the values on load).
  $effect(() => {
    if (!iframeLoaded) return;
    sendDownlink();
  });

  // The frontend registers its message listeners when its scripts run, so
  // a message posted before load could be lost. Send once the iframe
  // finished loading to guarantee the first frame converges.
  function handleLoad() {
    iframeLoaded = true;
    sendDownlink();
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
