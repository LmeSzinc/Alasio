<script lang="ts">
  import Check from "@lucide/svelte/icons/check";
  import CircleDotDashed from "@lucide/svelte/icons/circle-dot-dashed";
  import Clock from "@lucide/svelte/icons/clock";
  import EyeOff from "@lucide/svelte/icons/eye-off";
  import PlayOff from "@lucide/svelte/icons/play-off";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import Zap from "@lucide/svelte/icons/zap";
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import Button from "$lib/components/ui/button/button.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { t } from "$lib/i18n";
  import { fullTime, globalClock, shortTime } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
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
  let { class: className, config_name: _config_name, data, previewMode, workerState, onModeChange }: Props = $props();

  // Derive whether preview is active (not disabled)
  const isPreviewActive = $derived(previewMode !== "disable");

  // Internal decoded state from data protocol
  let displayState = $state<PreviewState>("preview");
  // Timestamp (ms, from the frame header) of the frame currently on screen; null = nothing
  // drawn. The header timestamp is the frame identity — there is no sequence number on the
  // wire — so this single value answers all three questions about the frame on screen: how
  // old it is (the timestamp overlay), whether there is one at all (`showImage`), and
  // whether an arriving frame is older than it (and must be dropped).
  let frameTime = $state<number | null>(null);

  // Frame canvas: the backing store is the frame's own size and the parent box scales it
  // through CSS (h-full/w-full + object-fit: contain), so neither window resize nor
  // devicePixelRatio changes need any JS — the compositor stretches the canvas raster,
  // exactly like it did for the <img> resource before.
  let canvasEl = $state<HTMLCanvasElement | null>(null);
  const HEADER_DECODER = new TextDecoder();

  /** True when the bitmap already on screen is newer than the given frame */
  const isSuperseded = (timestamp: number) => frameTime !== null && timestamp < frameTime;

  const clearFrame = (canvas: HTMLCanvasElement | null) => {
    // A null bitmap is the documented way to empty a bitmap renderer canvas; it keeps the
    // backing store size (resetting width/height would empty it too, but resize the store).
    canvas?.getContext("bitmaprenderer")?.transferFromImageBitmap(null);
    // Nothing is on screen any more: the next decoded frame is accepted whatever its age
    frameTime = null;
  };

  // Decode raw data (16-byte header + optional JPG bytes), extract state/timestamp and draw
  // the frame into the canvas
  $effect(() => {
    const raw = data;
    const canvas = canvasEl;
    if (!raw || !(raw instanceof ArrayBuffer) || raw.byteLength < 16) {
      // No valid data — keep current display state (do not reset)
      return;
    }

    // The data format: 8 bytes header (ASCII) + BigEndian Milliseconds (8 bytes) + optional JPG Bytes
    // Header: b'Preview_' (preview signal) or b'PreviewS' (stop signal)
    const header = HEADER_DECODER.decode(new Uint8Array(raw, 0, 8));
    const timestamp = Number(new DataView(raw).getBigUint64(8));

    if (header === "PreviewS") {
      // Stop signal received: empty the canvas
      displayState = "stopped";
      clearFrame(canvas);
      return;
    }
    if (header !== "Preview_") {
      // Unknown header
      displayState = "error";
      clearFrame(canvas);
      return;
    }

    if (!canvas) {
      // Canvas not mounted yet: this effect re-runs once bind:this assigns it
      return;
    }

    // A Uint8Array view avoids copying the payload (the Blob copies the bytes once anyway).
    // The decode itself runs off the main thread (~1ms for a 640x360 JPEG).
    createImageBitmap(new Blob([new Uint8Array(raw, 16)], { type: "image/jpeg" }))
      .then((bitmap) => {
        if (!canvas.isConnected || isSuperseded(timestamp)) {
          // The component is gone, or the frame on screen is newer (this decode finished
          // out of order): drop the bitmap instead of drawing it over the newer one
          bitmap.close();
          return;
        }
        const ctx = canvas.getContext("bitmaprenderer");
        if (!ctx) {
          bitmap.close();
          return;
        }
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          // The transferred bitmap becomes the canvas raster by itself, but it does NOT
          // update the width/height attributes — keep them truthful for debugging.
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
        }
        ctx.transferFromImageBitmap(bitmap);
        // The frame is on screen now: record its timestamp and show the canvas
        frameTime = timestamp;
        displayState = "preview";
      })
      .catch(() => {
        if (!canvas.isConnected || isSuperseded(timestamp)) {
          // Nothing to report: the frame on screen is newer than the one that failed
          return;
        }
        // Undecodable frame: report it instead of keeping a stale one
        displayState = "error";
        clearFrame(canvas);
      });
  });

  // Show the canvas only while it holds a frame and preview is enabled; otherwise the
  // placeholder below takes over (the canvas keeps its last frame in memory meanwhile)
  const showImage = $derived(displayState === "preview" && frameTime !== null && isPreviewActive);

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
  const diff = $derived(frameTime ? globalClock.now - frameTime : 0);
  // Show timestamp only if the image is older than 10 seconds.
  // Display format: hh:mm:ss.xxx
  const showTime = $derived(diff > 10000); // 10s
  // If the image is older than 12 hours, show the full date.
  // Display format: yy-mm-dd hh:mm:ss.xxx
  const isTooOld = $derived(diff > 12 * 60 * 60 * 1000); // 12h
  const timeStr = $derived(frameTime ? (isTooOld ? fullTime(frameTime) : shortTime(frameTime)) : "");
</script>

<div
  class={cn(
    "neushadow bg-card group relative flex flex-col items-center justify-center overflow-hidden rounded-lg",
    className,
  )}
>
  <!-- Frame canvas: backing store = the frame's own size, the card box scales it through
       CSS (object-contain, same letterboxing the <img> had) -->
  <canvas bind:this={canvasEl} class={cn("h-full w-full rounded-md object-contain", showImage ? "block" : "hidden")}
  ></canvas>

  {#if !showImage}
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
    {:else if !isPreviewActive}
      <div class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm italic">
        <EyeOff class="h-5 w-5" />
        {t.Overview.PreviewDisabled()}
      </div>
    {:else if workerState === "idle" || workerState === "restarting" || workerState === "resuming"}
      <!-- stopped for a graceful backend restart / queued for resume: not running -->
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
