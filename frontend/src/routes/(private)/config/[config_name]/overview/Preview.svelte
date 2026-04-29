<script lang="ts">
  import Button from "$lib/components/ui/button/button.svelte";
  import * as Popover from "$lib/components/ui/popover";
  import { t } from "$lib/i18n";
  import { fullTime, globalClock, shortTime } from "$lib/use/clock.svelte";
  import { screen } from "$lib/use/screen.svelte";
  import { useLocalStorage } from "$lib/use/useLocalStorage.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { previewClient } from "$lib/ws/preview.svelte";
  import { Check, Zap } from "@lucide/svelte";
  import { onDestroy, untrack } from "svelte";

  type Props = {
    class?: string;
    config_name: string;
  };
  let { class: className, config_name }: Props = $props();

  type PreviewMode = "realtime" | "normal" | "disable";

  // State for image display
  let imageUrl = $state<string | null>(null);
  let imageTime = $state<number | null>(null);

  // Global preview mode stored in localStorage (not per-config)
  const previewMode = useLocalStorage<PreviewMode>("preview_mode", "normal");

  // Subscribe to Preview topic using the specialized previewClient
  const topic = useTopic<ArrayBuffer>("Preview", previewClient);
  const rpc = topic.resilientRpc();
  const stopRpc = topic.rpc();
  globalClock.use();

  // Manage image object URL lifecycle
  function cleanupImage() {
    if (imageUrl) {
      URL.revokeObjectURL(imageUrl);
      imageUrl = null;
      imageTime = null;
    }
  }

  // Handle incoming preview data
  $effect(() => {
    const data = topic.data;
    if (!data) return;
    if (!(data instanceof ArrayBuffer)) return;

    // The data format: b'Preview' (7 bytes) + BigEndian Milliseconds (8 bytes) + JPG Bytes
    const view = new DataView(data);
    if (data.byteLength < 15) return;

    // Extract timestamp (BigInt64 at index 7)
    // We convert it to number (milliseconds)
    const timestamp = Number(view.getBigUint64(7));

    // Extract JPG data
    const imgBlob = new Blob([data.slice(15)], { type: "image/jpeg" });
    const newUrl = URL.createObjectURL(imgBlob);

    // Update state and cleanup old URL
    const oldUrl = untrack(() => imageUrl);
    imageUrl = newUrl;
    imageTime = timestamp;

    if (oldUrl) {
      URL.revokeObjectURL(oldUrl);
    }
  });

  // RPC subscription management
  // Use a separate non-resilient RPC for preview_stop to avoid re-sending it on reconnect.
  $effect(() => {
    if (screen.isHidden || previewMode.value === "disable") {
      stopRpc.call("preview_stop");
      // Clean up cached image when disabled
      cleanupImage();
    } else {
      const speed = previewMode.value === "realtime" ? "realtime" : "normal";
      rpc.call("preview_start", { name: config_name, speed });
    }
  });

  // Also cleanup when mode changes to disable while screen is visible
  $effect(() => {
    if (previewMode.value === "disable" && !screen.isHidden) {
      cleanupImage();
    }
  });

  onDestroy(() => {
    cleanupImage();
  });

  // Popover open state
  let popoverOpen = $state(false);

  // Preview mode display labels
  let modeOptions: { value: PreviewMode; label: string }[] = $derived([
    { value: "realtime", label: t.Dashboard.PreviewRealtime() },
    { value: "normal", label: t.Dashboard.PreviewNormal() },
    { value: "disable", label: t.Dashboard.PreviewDisable() },
  ]);

  // Timestamp formatting logic
  const diff = $derived(imageTime ? globalClock.now - imageTime : 0);
  // Show timestamp only if the image is older than 10 seconds.
  // Display format: hh:mm:ss.xxx
  const showTime = $derived(diff > 10000); // 10s
  // If the image is older than 12 hours, show the full date.
  // Display format: yy-mm-dd hh:mm:ss.xxx
  const isTooOld = $derived(diff > 12 * 60 * 60 * 1000); // 12h
  const timeStr = $derived(imageTime ? (isTooOld ? fullTime(imageTime) : shortTime(imageTime)) : "");

  // Determine if preview is active (not disabled)
  const isPreviewActive = $derived(previewMode.value !== "disable");
</script>

<div
  class={cn(
    "neushadow bg-card group relative flex flex-col items-center justify-center overflow-hidden rounded-lg",
    "aspect-video",
    className,
  )}
>
  {#if imageUrl && isPreviewActive}
    <img src={imageUrl} alt="Preview" class="h-full w-full rounded-md object-contain" />
  {:else if !isPreviewActive}
    <div class="text-muted-foreground flex h-full items-center justify-center text-sm italic">
      {t.Dashboard.PreviewDisabled()}
    </div>
  {:else}
    <div class="text-muted-foreground flex h-full items-center justify-center text-sm italic">
      {t.Dashboard.PreviewWaiting()}
    </div>
  {/if}

  <!-- Preview Mode Selector: Top Right as Popover (hidden by default, show on hover) -->
  <Popover.Root bind:open={popoverOpen}>
    <Popover.Trigger
      class={cn(
        "absolute top-3 right-3 z-20 flex h-8 w-8 items-center justify-center rounded-full border backdrop-blur-sm",
        "focus-visible:ring-ring ring-offset-background focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "opacity-0 transition-opacity group-hover:opacity-100 group-focus:opacity-100",
        previewMode.value === "disable"
          ? "text-muted-foreground border-transparent"
          : previewMode.value === "realtime"
            ? "border-yellow-500 text-yellow-500"
            : "border-blue-400 text-blue-400",
      )}
      aria-label="Preview Mode"
    >
      <Zap class={cn("h-4 w-4", previewMode.value === "realtime" && "fill-current")} />
    </Popover.Trigger>

    <Popover.Content class="w-48 p-1" align="end">
      <div class="space-y-1">
        {#each modeOptions as option}
          {@const variant = previewMode.value === option.value ? "default" : "ghost"}
          <Button
            class="w-full justify-between font-normal"
            {variant}
            onclick={() => {
              previewMode.value = option.value;
              popoverOpen = false;
            }}
          >
            {option.label}
            {#if previewMode.value === option.value}
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
