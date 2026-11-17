<script lang="ts">
  import { ModeWatcher } from "mode-watcher";
  import { type Component, onMount } from "svelte";
  import { Toaster } from "$lib/components/ui/sonner";
  import "$lib/dpi/state.svelte";
  // Side-effect imports: register the alasio:* downlink listeners at app
  // startup, before the alasio:ready handshake below triggers the host
  // re-send. Relying on implicit loading (i18n via the global t proxy,
  // theme via the header theme-toggle, dpi via the lazily loaded dev
  // settings page) would make the registration timing fragile — a
  // listener registered after the handshake re-send loses the host value
  // forever (the dpi checkbox once kept its default checked state).
  import "$lib/i18n/state.svelte";
  import "$lib/theme/state.svelte";
  import { isElectronSession } from "$lib/use/useElectronEnv.svelte";
  import { DragRegionContext, startDragRegionReporter } from "$lib/window/dragRegion.svelte";
  import "../app.css";

  let { children } = $props();

  // The drag region context lives at the root: both the public and the private
  // layouts declare into it, and the reporter started below is the only writer
  // of the host message.
  const dragRegion = DragRegionContext.init();

  // Dev-only outline of the reported drag region (see DragRegionOutline).
  // Loaded through a dynamic import guarded by import.meta.env.DEV, so the
  // component is never bundled into a production build.
  let DragRegionOutline: Component | undefined = $state();

  onMount(() => {
    if (import.meta.env.DEV) {
      import("$lib/window/DragRegionOutline.svelte")
        .then((mod) => {
          DragRegionOutline = mod.default;
        })
        .catch((err) => {
          console.error("Failed to load DragRegionOutline:", err);
        });
    }

    return startDragRegionReporter(dragRegion);
  });

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
<!-- The app header (AppHeader, h-12 = 3rem) is fixed at the top of every
     private page and the toasts are top aligned, so the top offset clears it
     instead of letting them overlap: header height + a gap (1rem on desktop,
     0.5rem on mobile, like the 24px / 16px of sonner's own defaults). Every
     toast of the app inherits it, a custom toast included. -->
<Toaster
  richColors
  closeButton
  duration={5000}
  position="top-right"
  expand={true}
  offset={{ top: "4rem" }}
  mobileOffset={{ top: "3.5rem" }}
/>
{@render children()}

<!-- Dev-only: the drag region the host would make draggable. The module is
     only loaded (and only bundled) in dev builds; see the dynamic import above. -->
{#if DragRegionOutline}
  <DragRegionOutline />
{/if}
