<script lang="ts">
  import { onMount } from "svelte";
  import { useSharedState } from "$lib/useSharedState.svelte";
  import { applyDragRegion, resetDragRegion, windowFrame } from "$lib/windowFrame.svelte";

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
  // preference). The language pair mirrors the `alasio:lang` downlink
  // (display value + config value) so the first paint already renders in
  // the host's language instead of the language the frontend guesses from
  // the browser until the downlink arrives. Deliberately captured once,
  // not reactive: rewriting the src would reload the iframe. Changes
  // after load are propagated through the alasio:* downlinks instead.
  let iframeSrc = $state(
    `http://127.0.0.1:${sharedState.backendPort}/?embedded=electron&theme=${sharedState.displayTheme}` +
      `&themeConfig=${sharedState.configTheme}` +
      `&lang=${sharedState.displayLang}&langConfig=${sharedState.configLang}` +
      // The window controls are overlaid on the frontend header; their reserve
      // width is passed in the URL so the very first paint already keeps them
      // clear (`alasio:frame` corrects it once they are measured).
      `&controlsWidth=${windowFrame.controlsWidth}`,
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
  // Both the lang and the theme message also carry the host config value, so
  // the frontend converges with the host instead of keeping its own guess
  // (its config may differ on first run), and so a 'system' preference is
  // not flattened into the concrete value it currently resolves to -- the
  // page has to keep following the os when the user picked 'system'.
  // Sending the same value again is harmless: the frontend no-ops on
  // identical values. Dpi scaling has a single value (no config/display
  // split) and is sent as-is.
  function sendDownlink() {
    const frame = iframe;
    if (!frame?.contentWindow) return;
    const origin = frontendOrigin;
    frame.contentWindow.postMessage(
      { type: "alasio:lang", lang: sharedState.displayLang, configLang: sharedState.configLang },
      origin,
    );
    frame.contentWindow.postMessage(
      { type: "alasio:theme", theme: sharedState.displayTheme, configTheme: sharedState.configTheme },
      origin,
    );
    frame.contentWindow.postMessage({ type: "alasio:dpi-scaling", dpiScaling: sharedState.dpiScaling }, origin);
    frame.contentWindow.postMessage({ type: "alasio:frame", controls: { width: windowFrame.controlsWidth } }, origin);
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

  // The window controls are measured once they mounted (and remeasured if
  // their size ever changes), which can happen after the ready handshake.
  // Pushing the value here keeps the frontend's reserve width exact instead of
  // relying on the URL value baked at first paint.
  $effect(() => {
    const width = windowFrame.controlsWidth;
    if (!iframeLoaded) return;
    const frame = iframe;
    if (!frame?.contentWindow) return;
    frame.contentWindow.postMessage({ type: "alasio:frame", controls: { width } }, frontendOrigin);
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
      } else if (data.type === "alasio:dpi-scaling" && typeof data.dpiScaling === "boolean") {
        window.electronAPI.setDpiScaling(data.dpiScaling);
      } else if (data.type === "alasio:drag-region") {
        // Where the frontend header may be dragged. `null` means it declares
        // nothing (no header, or the layout unmounted), so the default band
        // applies again. Malformed payloads keep the previous band.
        if (data.region === null) {
          resetDragRegion();
        } else {
          applyDragRegion(data.region);
        }
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
    class="w-full flex-1 border-0 opacity-0 transition-opacity duration-150"
    class:opacity-100={iframeVisible}
    title="Alasio App"
    sandbox="allow-scripts allow-same-origin allow-forms"
  ></iframe>
</div>
