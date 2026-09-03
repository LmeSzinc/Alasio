<script lang="ts">
  import { onMount, untrack } from "svelte";
  import LoaderCircle from "@lucide/svelte/icons/loader-circle";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import StartupCard from "$lib/components/StartupCard.svelte";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { t } from "$lib/i18n";
  import { getDevOverride, useSharedState } from "$lib/useSharedState.svelte";

  const sharedState = useSharedState();
  // Startup status comes from shared state: the main process publishes
  // "starting" at the beginning of every launch attempt, "success" once
  // the backend is up, "failed" when an attempt settled with an error.
  // The failure hint is exclusive to "failed": while an attempt is
  // running (or before the first one) the footer shows the spinner state.
  const backendStatus = $derived(sharedState.backendStatus);
  const failed = $derived(backendStatus === "failed");
  const starting = $derived(backendStatus === "starting");

  // Dev preview mock logs, in chronological order (oldest first). The page
  // stores lines newest-first for the column-reverse layout, so the mock
  // is streamed in chronological order (each line unshifted one by one,
  // see fillDevLogs) and the newest line always lands at the bottom edge.
  // Previewed through DevRouteSwitcher URLs (/loading?backendSuccess=true|false).
  // Guarded by import.meta.env.DEV so the build folds them to empty arrays
  // and the mock data never reaches production bundles (the preview path
  // itself is already dead in production: getDevOverride returns null
  // there).
  // The filler prefix (meaningless lines) makes the log area overflow so
  // the preview shows the scrolling behavior.
  const DEV_LOG_FILLER = import.meta.env.DEV
    ? Array.from({ length: 40 }, (_, i) => `[Supervisor] dev preview filler line ${String(i + 1).padStart(2, "0")}`)
    : [];
  const DEV_LOGS = import.meta.env.DEV
    ? [
        ...DEV_LOG_FILLER,
        "Loading deploy.yaml ...",
        "[Supervisor] Electron mode, port 22267",
        "[Supervisor] Starting backend process...",
        "Loading module: alasio.backend",
        "Registering routes: /api, /topic, /assets",
        "Running on http://127.0.0.1:22267 (CTRL + C to quit)",
      ]
    : [];
  const DEV_LOGS_FAILED = import.meta.env.DEV
    ? [
        ...DEV_LOG_FILLER,
        "Loading deploy.yaml ...",
        "[Supervisor] Electron mode, port 22267",
        "[Supervisor] Starting backend process...",
        "Loading module: alasio.backend",
        "Registering routes: /api, /topic, /assets",
        "[Supervisor] ERROR: Backend exited before ready (code: 1)",
      ]
    : [];

  // Log lines are prepended (newest first) and the column-reverse layout
  // puts the first DOM child — the newest line — at the container's
  // bottom edge, so the newest line is always the last row of the log.
  // The flex container is a plain block child of the scroll viewport, so
  // its content overflows the viewport downward: the scroll range starts
  // at the oldest line (scrollTop 0) and scrolling to the bottom
  // (scrollTop = scrollHeight) pins the newest line at the bottom edge
  // while older lines are pushed upwards as new ones arrive.
  let logs = $state<string[]>([]);
  let retrying = $state(false);
  let logViewport: HTMLDivElement | null = $state(null);

  // Auto-scroll to the newest line whenever the log grows: the newest
  // line is the last row of the content (see above), so just scroll the
  // viewport to the bottom.
  $effect(() => {
    if (logs.length > 0 && logViewport) {
      logViewport.scrollTop = logViewport.scrollHeight;
    }
  });

  // Dev preview fill generation. Bumping it cancels any in-flight fill
  // loop (see fillDevLogs): used when the preview switches between
  // success/failed, when retry starts a new fill, or when the component
  // unmounts mid-fill, so stale timers never keep appending lines.
  let devFillGeneration = 0;
  // Interval between two mock log lines while filling (0.05s), so the dev
  // preview log streams in line by line like a real backend log stream.
  const DEV_LOG_INTERVAL_MS = 50;

  function devLogDelay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // Dev preview: append mock log lines one by one, DEV_LOG_INTERVAL_MS
  // apart, so the preview animates like a real backend log stream. Each
  // unshift also re-triggers the auto-scroll effect above, pinning the
  // newest line at the bottom edge while older lines are pushed upwards.
  // Must be called outside a tracking context (see the $effect below), or
  // wrapped in untrack: the first unshift reads `logs` through the $state
  // proxy, and an effect that reads the state it mutates re-runs forever
  // (svelte throws effect_update_depth_exceeded).
  async function fillDevLogs(lines: string[]) {
    const generation = ++devFillGeneration;
    logs = [];
    for (const line of lines) {
      // A newer fill (preview switch, retry or unmount) superseded this
      // one; stop inserting to keep the log state consistent.
      if (generation !== devFillGeneration) return;
      logs.unshift(line);
      await devLogDelay(DEV_LOG_INTERVAL_MS);
    }
  }

  // Dev preview: fill the mock log for the URL backendSuccess flag, and
  // re-fill whenever the preview switches between success/failed
  // (component instance is reused across query changes). No-op in real
  // runs.
  $effect(() => {
    const override = getDevOverride();
    if (override?.route !== "loading" || override.backendSuccess === undefined) return;
    // untrack: the fill mutates `logs`; without it the first unshift would
    // register `logs` as a dependency of this effect and every inserted
    // line would re-trigger it, restarting the fill forever.
    untrack(() => {
      void fillDevLogs(override.backendSuccess ? DEV_LOGS : DEV_LOGS_FAILED);
    });
    // Cancel the in-flight fill when this effect re-runs (the preview
    // switched) or when the component unmounts mid-fill.
    return () => {
      devFillGeneration++;
    };
  });

  onMount(() => {
    // Dev preview shows mock logs only; skip the real backend log stream.
    const override = getDevOverride();
    if (override?.route === "loading" && override.backendSuccess !== undefined) return;

    // Subscribe AFTER registering the live listener: the subscribe reply
    // replays the whole main-process buffer (chronological snapshot, see
    // startup-log.ts) so no pre-mount line is lost - the main process
    // does not push before the subscription, and pushes share the IPC
    // ordering with the reply, so the snapshot is complete up to the
    // subscribe moment. The snapshot replaces the whole view (reversed
    // to newest-first); a push racing the reply is also still in the
    // buffer, so the replacement stays consistent.
    const unsubscribe = window.electronAPI.onBackendLog((log: string) => {
      logs.unshift(log);
    });
    void window.electronAPI.subscribeBackendLogs().then((snapshot: string[]) => {
      logs = snapshot.reverse();
    });

    return unsubscribe;
  });

  // Retry wipes the previous attempt's log, then the log accumulates
  // from scratch again (the main process empties its buffer and flips
  // the status back to "starting" at the beginning of the new attempt,
  // which hides the failure hint and shows the spinner state). In dev
  // preview the retry is simulated locally: the mock log is re-filled
  // without touching the real backend.
  async function retry() {
    logs = [];
    retrying = true;
    const override = getDevOverride();
    if (override?.route === "loading" && override.backendSuccess !== undefined) {
      void fillDevLogs(override.backendSuccess ? DEV_LOGS : DEV_LOGS_FAILED);
      retrying = false;
      return;
    }
    try {
      await window.electronAPI.startBackend();
    } finally {
      retrying = false;
    }
  }
</script>

<StartupCard title="Alasio" desc={t.Loading.StartingBackend()} class="">
  <div class="flex h-full w-full flex-col">
    <!-- Log area: newest line at the bottom, older lines pushed upwards.
         Styling follows the frontend overview LogDisplay/LogData: card
         surface, scroll area, and a per-line hover highlight. The backend
         log has no level/time, so only the shared skeleton is kept. -->
    <ScrollArea bind:viewportRef={logViewport} type="always" class="bg-card min-h-0 w-full flex-1 border-t border-b">
      <div class="flex flex-col-reverse">
        {#each logs as log}
          <div
            class="hover:bg-muted/50 hover:shadow-muted-foreground/15 block px-1 py-0.25 font-mono text-xs hover:shadow-[inset_0_1px_0_0_currentColor,inset_0_-1px_0_0_currentColor]"
          >
            <pre class="text-muted-foreground m-0 break-all whitespace-pre-wrap">{log}</pre>
          </div>
        {/each}
      </div>
    </ScrollArea>
    <!-- Status bar below the log: spinner + "Starting backend..." while
         an attempt is running; the failure hint and a retry button when
         an attempt failed; nothing after a successful start (the page
         has already navigated away by then). -->
    <div class="mt-2 flex h-8 w-full shrink-0 items-center justify-end gap-4">
      {#if failed}
        <div class="flex items-center gap-2">
          <TriangleAlert class="text-destructive size-4" />
          <p class="ml-0">{t.Loading.BackendStartFailed()}</p>
        </div>
        <Button onclick={retry} disabled={retrying} class="h-8 w-16 font-semibold">
          {t.Error.Retry()}
        </Button>
      {:else if starting}
        <div class="flex items-center gap-2">
          <LoaderCircle class="text-muted-foreground size-4 animate-spin" />
          <p class="text-muted-foreground ml-0">{t.Loading.StartingBackend()}</p>
        </div>
      {/if}
    </div>
  </div>
</StartupCard>
