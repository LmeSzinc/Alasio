<script lang="ts">
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import PreviewDisplay from "$private/config/[config_name]/overview/PreviewDisplay.svelte";

  // Helper to wrap raw JPEG bytes into the Preview_ protocol format
  function wrapPreviewData(jpegBytes: ArrayBuffer, header: string, timestamp: number): ArrayBuffer {
    const encoder = new TextEncoder();
    const headerBytes = encoder.encode(header.padEnd(8, "\0").slice(0, 8));
    const tsBuf = new ArrayBuffer(8);
    new DataView(tsBuf).setBigUint64(0, BigInt(timestamp));

    const combined = new Uint8Array(headerBytes.length + tsBuf.byteLength + jpegBytes.byteLength);
    combined.set(headerBytes, 0);
    combined.set(new Uint8Array(tsBuf), headerBytes.length);
    combined.set(new Uint8Array(jpegBytes), headerBytes.length + tsBuf.byteLength);
    return combined.buffer;
  }

  // Load the sample image from server
  let sampleImageBuffer = $state<ArrayBuffer | null>(null);

  $effect(() => {
    fetch("/sample-preview.jpg")
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        sampleImageBuffer = buf;
      })
      .catch(() => {});
  });

  // Fall back to empty buffer when image hasn't loaded yet
  const imageBuf = $derived(sampleImageBuffer ?? new ArrayBuffer(0));

  type PreviewMode = "realtime" | "normal" | "disable";

  // Per-card interactive preview mode state
  // 0:Image 1:Waiting 2:Stopped 3:Disabled 4:NotRunning 5:Error
  let cardModes = $state<PreviewMode[]>(["normal", "normal", "normal", "disable", "normal", "normal"]);

  type TestCase = {
    label: string;
    data: ArrayBuffer | null;
    workerState: WORKER_STATE;
  };

  const cases: TestCase[] = $derived([
    {
      label: "Image",
      data: wrapPreviewData(imageBuf, "Preview_", Date.now()),
      workerState: "running",
    },
    {
      label: "Waiting",
      data: null,
      workerState: "running",
    },
    {
      label: "Stopped",
      data: wrapPreviewData(new ArrayBuffer(0), "PreviewS", Date.now()),
      workerState: "running",
    },
    {
      label: "Disabled",
      data: wrapPreviewData(imageBuf, "Preview_", Date.now()),
      workerState: "running",
    },
    {
      label: "Not Running",
      data: null,
      workerState: "idle",
    },
    {
      label: "Error",
      data: wrapPreviewData(new ArrayBuffer(0), "xxxxBad", Date.now()),
      workerState: "running",
    },
  ]);
</script>

<div class="flex flex-wrap gap-4 p-4">
  {#each cases as c, i}
    <div class="w-[360px]">
      <div class="h-[202px]">
        <PreviewDisplay
          class="h-full w-full"
          config_name={c.label}
          data={c.data}
          previewMode={cardModes[i]}
          workerState={c.workerState}
          onPreviewStart={() => {}}
          onPreviewStop={() => {}}
          onModeChange={(mode) => {
            cardModes[i] = mode;
          }}
        />
      </div>
      <p class="text-muted-foreground mt-1 text-center text-xs">{c.label}</p>
    </div>
  {/each}
</div>
