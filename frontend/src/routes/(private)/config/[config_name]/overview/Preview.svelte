<script lang="ts">
  import type { WORKER_STATE } from "$lib/components/aside/types";
  import type { PreviewMode } from "./types";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import { previewClient } from "$lib/ws/preview.svelte";
  import { screen } from "$lib/use/screen.svelte";
  import { useLocalStorage } from "$lib/use/useLocalStorage.svelte";
  import PreviewDisplay from "./PreviewDisplay.svelte";

  type Props = {
    class?: string;
    config_name: string;
  };
  let { class: className, config_name }: Props = $props();

  // Track whether the config's worker is running
  const workerClient = useTopic<Record<string, WORKER_STATE> | undefined>("Worker");
  const workerState = $derived(workerClient.data?.[config_name] || "idle");

  // Global preview mode stored in localStorage (not per-config)
  const previewMode = useLocalStorage<PreviewMode>("preview_mode", "normal");

  // Subscribe to Preview topic using the specialized previewClient
  const topic = useTopic<ArrayBuffer>("Preview", previewClient);
  const rpc = topic.resilientRpc();
  const stopRpc = topic.rpc();

  // RPC subscription management — calls preview_start/preview_stop based on mode & screen visibility.
  // Uses a separate non-resilient RPC for preview_stop to avoid re-sending it on reconnect.
  $effect(() => {
    if (screen.isHidden || previewMode.value === "disable") {
      stopRpc.call("preview_stop");
    } else {
      const speed = previewMode.value === "realtime" ? "realtime" : "normal";
      rpc.call("preview_start", { name: config_name, speed });
    }
  });
</script>

<PreviewDisplay
  class={cn(className)}
  {config_name}
  data={topic.data ?? null}
  previewMode={previewMode.value}
  {workerState}
  onModeChange={(mode) => {
    previewMode.value = mode;
  }}
  onPreviewStart={() => {
    rpc.call("preview_start", { name: config_name, speed: "normal" });
  }}
  onPreviewStop={() => {
    stopRpc.call("preview_stop");
  }}
/>
