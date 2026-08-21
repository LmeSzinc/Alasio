<script lang="ts">
  import { onMount } from "svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
  let iframe: HTMLIFrameElement | undefined = $state();
  // The iframe's initial empty document is white until the frontend's
  // first dark paint (its pre-paint script applies the theme passed in
  // the URL). Keep it hidden until it is loaded (or the frontend signals
  // ready) so no white flash shows between the loading page and the app;
  // the parent background stays dark behind it.
  let iframeVisible = $state(false);

  // Initial iframe URL. The theme query is read by the frontend's
  // pre-paint script in app.html, so the embedded app's very first paint
  // already matches the host display theme (mode-watcher alone cannot
  // know it: in this SPA build its FOUC script is absent from the served
  // HTML, and its initial mode only comes from localStorage/system
  // preference). Deliberately captured once, not reactive: rewriting the
  // src would reload the iframe. Theme changes after load are propagated
  // through the alasio:theme downlink instead.
  let iframeSrc = $state(
    `http://127.0.0.1:${sharedState.backendPort}/?embedded=electron&theme=${sharedState.displayTheme}`,
  );

  // The embedded frontend origin. Downlink messages are only sent to this
  // origin, and uplink messages are only accepted from it.
  const frontendOrigin = $derived(`http://127.0.0.1:${sharedState.backendPort}`);

  // Whether the iframe has finished loading the backend page. Until the
  // load event the iframe is still on its initial about:blank document,
  // whose origin is inherited from the parent (app://bundle); posting
  // with a strict targetOrigin then throws "target origin does not match
  // the recipient window's origin" instead of delivering. Downlink
  // messages are only sent after the backend page is actually loaded.
  // Reactive ($state): the downlink effect below reads it, so flipping it
  // on load reruns the effect and registers the display-value deps.
  let iframeLoaded = $state(false);

  // Send the current display values down to the embedded frontend.
  // The lang message also carries the host config value so the frontend's
  // configLang converges with the host (its own guess from cookie/browser
  // may differ on first run). Sending the same value again is harmless:
  // the frontend no-ops on identical values.
  function sendDownlink() {
    const frame = iframe;
    if (!frame?.contentWindow) return;
    const origin = frontendOrigin;
    frame.contentWindow.postMessage(
      { type: "alasio:lang", lang: sharedState.displayLang, configLang: sharedState.configLang },
      origin,
    );
    frame.contentWindow.postMessage({ type: "alasio:theme", theme: sharedState.displayTheme }, origin);
  }

  // Send whenever the display values change after the iframe finished
  // loading (the load handler below sends the values on load). Note the
  // iframe load event can fire before the frontend registered its message
  // listeners (the frontend starts through dynamic imports), so that first
  // downlink may be lost; the frontend's "alasio:ready" handshake (sent
  // once its listeners are registered) re-triggers this through the
  // listener in onMount.
  $effect(() => {
    if (!iframeLoaded) return;
    sendDownlink();
  });

  // The frontend registers its message listeners when its scripts run, so
  // a message posted before load could be lost. Send once the iframe
  // finished loading to guarantee the first frame converges.
  function handleLoad() {
    iframeLoaded = true;
    iframeVisible = true;
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
      } else if (data.type === "alasio:ready") {
        // Handshake from the embedded frontend: it finished starting and
        // registered its listeners. The load-event downlink may have been
        // lost (the iframe load event can fire before the frontend's
        // dynamic import chain finished), so re-send the current display
        // values now. Also reveal the iframe: its first paint is already
        // themed (pre-paint script), so hiding it any longer is
        // unnecessary.
        iframeVisible = true;
        sendDownlink();
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
    src={iframeSrc}
    class="flex-1 w-full border-0 opacity-0 transition-opacity duration-150"
    class:opacity-100={iframeVisible}
    title="Alasio App"
  ></iframe>
</div>
