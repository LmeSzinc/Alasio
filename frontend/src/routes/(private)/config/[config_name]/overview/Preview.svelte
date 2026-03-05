<script lang="ts">
  import { globalClock } from "$lib/use/clock.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { previewClient } from "$lib/ws/preview.svelte";
  import { Zap } from "@lucide/svelte";
  import { onDestroy, untrack } from "svelte";

  type Props = {
    class?: string;
    config_name: string;
  };
  let { class: className, config_name }: Props = $props();

  // State for image display
  let imageUrl = $state<string | null>(null);
  let imageTime = $state<number | null>(null);
  let isRealtime = $state(false);

  // Subscribe to Preview topic using the specialized previewClient
  const topic = useTopic<ArrayBuffer>("Preview", previewClient);
  const rpc = topic.resilientRpc();
  globalClock.use()

  // Manage image object URL lifecycle
  function cleanupImage() {
    if (imageUrl) {
      URL.revokeObjectURL(imageUrl);
      imageUrl = null;
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
  $effect(() => {
    const speed = isRealtime ? "realtime" : "normal";
    rpc.call("preview_start", { name: config_name, speed });
  });

  onDestroy(() => {
    cleanupImage();
  });

  // Timestamp formatting logic
  const diff = $derived(imageTime ? globalClock.now - imageTime : 0);
  // Show timestamp only if the image is older than 10 seconds.
  // Display format: hh:mm:ss.xxx
  const showTime = $derived(diff > 10000); // 10s
  // If the image is older than 12 hours, show the full date.
  // Display format: yy-mm-dd hh:mm:ss.xxx
  const isTooOld = $derived(diff > 12 * 60 * 60 * 1000); // 12h

  function formatTimestamp(ts: number, full: boolean) {
    const date = new Date(ts);
    const pad = (n: number) => n.toString().padStart(2, "0");
    const padMs = (n: number) => n.toString().padStart(3, "0");

    const h = pad(date.getHours());
    const m = pad(date.getMinutes());
    const s = pad(date.getSeconds());
    const ms = padMs(date.getMilliseconds());

    if (full) {
      const y = date.getFullYear().toString().slice(-2);
      const mo = pad(date.getMonth() + 1);
      const d = pad(date.getDate());
      return `${y}-${mo}-${d} ${h}:${m}:${s}.${ms}`;
    }
    return `${h}:${m}:${s}.${ms}`;
  }

  const timeStr = $derived(imageTime ? formatTimestamp(imageTime, isTooOld) : "");
</script>

<div
  class={cn(
    "neushadow bg-card group relative flex flex-col items-center justify-center overflow-hidden rounded-lg",
    "aspect-video",
    className,
  )}
>
  {#if imageUrl}
    <img src={imageUrl} alt="Preview" class="h-full w-full rounded-md object-contain" />
  {:else}
    <div class="text-muted-foreground flex h-full items-center justify-center text-sm italic">
      Waiting for preview...
    </div>
  {/if}

  <!-- Realtime Toggle: Top Right -->
  <button
    class={cn(
      "absolute top-3 right-3 z-20 flex h-8 w-8 items-center justify-center rounded-full border",
      "hover:bg-accent backdrop-blur-sm",
      isRealtime
        ? "border-yellow-500 text-yellow-500 opacity-100"
        : "text-muted-foreground border-transparent opacity-0 group-hover:opacity-100 group-focus:opacity-100",
    )}
    onclick={() => (isRealtime = !isRealtime)}
    title={isRealtime ? "Realtime Enabled" : "Enable Realtime"}
  >
    <Zap class={cn("h-4 w-4", isRealtime && "fill-current")} />
  </button>

  <!-- Timestamp: Bottom Right -->
  {#if showTime && timeStr}
    <div
      class="bg-background/40 text-foreground/80 absolute right-2 bottom-2 rounded px-1.5 py-0.5 font-mono text-xs backdrop-blur-md"
    >
      {timeStr}
    </div>
  {/if}
</div>
