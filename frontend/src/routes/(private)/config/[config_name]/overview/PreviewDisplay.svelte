<script lang="ts">
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import Button from "$lib/components/ui/button/button.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { t } from "$lib/i18n";
  import { fullTime, globalClock, shortTime } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
  import { Check, CircleDotDashed, Clock, EyeOff, PlayOff, TriangleAlert, Zap } from "@lucide/svelte";
  import { onDestroy, untrack } from "svelte";
  import type { PreviewMode, PreviewState } from "./types";

  type Props = {
    class?: string;
    config_name: string;
    /** Raw ArrayBuffer from the Preview topic (16-byte header + optional JPG bytes) */
    data: ArrayBuffer | null;
    previewMode: PreviewMode;
    workerState: WORKER_STATE;
    onPreviewStart: () => void;
    onPreviewStop: () => void;
    onModeChange: (mode: PreviewMode) => void;
  };
  let {
    class: className,
    config_name: _config_name,
    data,
    previewMode,
    workerState,
    onModeChange,
  }: Props = $props();

  // Derive whether preview is active (not disabled)
  const isPreviewActive = $derived(previewMode !== "disable");

  // Internal decoded state from data protocol
  let displayState = $state<PreviewState>("preview");
  let imageTime = $state<number | null>(null);
  let imageUrl = $state<string | null>(null);

  // Decode raw data (16-byte header + optional JPG bytes), extract state/timestamp and create blob URL
  $effect(() => {
    const raw = data;
    if (!raw || !(raw instanceof ArrayBuffer) || raw.byteLength < 16) {
      // No valid data — keep current display state (do not reset)
      return;
    }

    // The data format: 8 bytes header (ASCII) + BigEndian Milliseconds (8 bytes) + optional JPG Bytes
    // Header: b'Preview_' (preview signal) or b'PreviewS' (stop signal)
    const view = new DataView(raw);
    const header = new TextDecoder().decode(raw.slice(0, 8));
    const timestamp = Number(view.getBigUint64(8));
    const prevUrl = untrack(() => imageUrl);

    if (header === "PreviewS") {
      // Stop signal received
      displayState = "stopped";
      imageTime = timestamp;
      if (prevUrl) {
        URL.revokeObjectURL(prevUrl);
      }
      imageUrl = null;
    } else if (header === "Preview_") {
      // Preview image signal
      displayState = "preview";
      imageTime = timestamp;
      const imgBlob = new Blob([raw.slice(16)], { type: "image/jpeg" });
      const newUrl = URL.createObjectURL(imgBlob);
      imageUrl = newUrl;
      if (prevUrl) {
        URL.revokeObjectURL(prevUrl);
      }
    } else {
      // Unknown header
      displayState = "error";
      imageTime = timestamp;
      if (prevUrl) {
        URL.revokeObjectURL(prevUrl);
      }
      imageUrl = null;
    }
  });

  // Clean up blob URL on destroy
  onDestroy(() => {
    if (imageUrl) {
      URL.revokeObjectURL(imageUrl);
    }
  });

  // Popover open state
  let popoverOpen = $state(false);

  // Preview mode display labels
  let modeOptions: { value: PreviewMode; label: string }[] = $derived([
    { value: "realtime", label: t.Overview.PreviewRealtime() },
    { value: "normal", label: t.Overview.PreviewNormal() },
    { value: "disable", label: t.Overview.PreviewDisable() },
  ]);

  // Timestamp formatting logic
  globalClock.use();
  const diff = $derived(imageTime ? globalClock.now - imageTime : 0);
  // Show timestamp only if the image is older than 10 seconds.
  // Display format: hh:mm:ss.xxx
  const showTime = $derived(diff > 10000); // 10s
  // If the image is older than 12 hours, show the full date.
  // Display format: yy-mm-dd hh:mm:ss.xxx
  const isTooOld = $derived(diff > 12 * 60 * 60 * 1000); // 12h
  const timeStr = $derived(imageTime ? (isTooOld ? fullTime(imageTime) : shortTime(imageTime)) : "");
</script>

<div
  class={cn(
    "neushadow bg-card group relative flex flex-col items-center justify-center overflow-hidden rounded-lg",
    className,
  )}
>
  {#if displayState === "error"}
    <div class="text-destructive flex h-full flex-col items-center justify-center gap-2 text-sm italic">
      <TriangleAlert class="h-5 w-5" />
      {t.Overview.PreviewError()}
    </div>
  {:else if displayState === "stopped"}
    <div class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm italic">
      <CircleDotDashed class="h-5 w-5" />
      {t.Overview.PreviewStopped()}
    </div>
  {:else if imageUrl && isPreviewActive}
    <img src={imageUrl} alt="Preview" class="h-full w-full rounded-md object-contain" />
  {:else if !isPreviewActive}
    <div class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm italic">
      <EyeOff class="h-5 w-5" />
      {t.Overview.PreviewDisabled()}
    </div>
  {:else if workerState === "idle"}
    <div class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm italic">
      <PlayOff class="h-5 w-5" />
      {t.Overview.PreviewNotRunning()}
    </div>
  {:else}
    <div class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm italic">
      <Clock class="h-5 w-5" />
      {t.Overview.PreviewWaiting()}
    </div>
  {/if}

  <!-- Preview Mode Selector: Top Right as Popover (hidden by default, show on hover) -->
  <Popover.Root bind:open={popoverOpen}>
    <Popover.Trigger
      class={cn(
        "absolute top-3 right-3 z-20 flex h-8 w-8 items-center justify-center rounded-full border backdrop-blur-sm",
        "focus-visible:ring-ring ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "opacity-0 transition-opacity group-hover:opacity-100 group-focus:opacity-100",
        previewMode === "disable"
          ? "text-muted-foreground border-muted-foreground"
          : previewMode === "realtime"
            ? "border-yellow-500 text-yellow-500"
            : "border-blue-400 text-blue-400",
      )}
      aria-label="Preview Mode"
    >
      <Zap class={cn("h-4 w-4", previewMode === "realtime" && "fill-current")} />
    </Popover.Trigger>

    <Popover.Content class="w-48 p-1" align="end">
      <div class="space-y-1">
        {#each modeOptions as option}
          {@const variant = previewMode === option.value ? "default" : "ghost"}
          <Button
            class="w-full justify-between font-normal"
            {variant}
            onclick={() => {
              onModeChange(option.value);
              popoverOpen = false;
            }}
          >
            {option.label}
            {#if previewMode === option.value}
              <Check class="h-4 w-4" />
            {/if}
          </Button>
        {/each}
      </div>
    </Popover.Content>
  </Popover.Root>

  <!-- Timestamp: Bottom Right -->
  {#if showTime && timeStr && isPreviewActive}
    <div
      class="bg-background/40 text-foreground/85 absolute right-2 bottom-2 rounded px-1.5 py-0.5 font-mono text-xs backdrop-blur-md"
    >
      {timeStr}
    </div>
  {/if}
</div>
