<script lang="ts">
  import { onMount } from "svelte";
  import { Toaster } from "$lib/components/ui/sonner";
  import { ModeWatcher } from "mode-watcher";
  import { isElectronSession } from "$lib/use/useElectronEnv.svelte";
  import "../app.css";

  let { children } = $props();

  // Electron handshake: the host (webapp renderer) sends its first
  // alasio:* downlink on iframe load, but the frontend's message listeners
  // are only registered after its dynamic import chain finishes — which
  // can be later than the load event, so that first downlink is silently
  // lost. Once the app is fully started (root layout mounted, all
  // static-import listeners registered), tell the host we are ready so it
  // re-sends the current display values. Only embedded sessions send this;
  // remote browsers have no parent container, so the message goes nowhere
  // (spoofing ?embedded=electron is harmless: the host validates the
  // sender origin before acting).
  onMount(() => {
    if (isElectronSession) {
      window.parent.postMessage({ type: "alasio:ready" }, "*");
    }
  });
</script>

<ModeWatcher />
<Toaster richColors closeButton duration={5000} position="top-right" expand={true} />
{@render children()}
