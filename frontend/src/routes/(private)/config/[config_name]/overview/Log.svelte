<script lang="ts">
  import Button from "$lib/components/ui/button/button.svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import LogData from "./LogData.svelte";
  import type { LogDataProps } from "./types";

  type Props = {
    class?: string;
  };
  let { class: className }: Props = $props();
  let logContainer: HTMLDivElement | null = $state(null);
  let isInitial = $state(true);

  // Subscribe to Log topic
  const logClient = useTopic<LogDataProps[]>("Log");

  // auto scroll on data changes
  let scrollRAF: number | null = null;
  function scrollToBottom() {
    scrollRAF = null;
    if (logContainer) {
      logContainer.scrollTop = logContainer.scrollHeight;
      logContainer.scrollLeft = 0;
      if (isInitial && logClient.data && logClient.data.length > 0) {
        isInitial = false;
      }
    }
  }
  $effect(() => {
    // tracking the length of logs
    const length = logClient.data?.length ?? 0;
    if (logContainer && scrollRAF === null && length > 0) {
      scrollRAF = requestAnimationFrame(scrollToBottom);
    }
  });
</script>

<ScrollArea
  class={cn("bg-card neushadow @container relative h-full max-h-screen w-full rounded-lg px-2.5 py-4", className)}
  orientation="both"
  bind:viewportRef={logContainer}
>
  <div class={cn("flex flex-col", isInitial && logClient.data && logClient.data.length > 0 && "invisible")}>
    {#if logClient.data}
      {#each logClient.data as log (log)}
        <LogData {...log} />
      {/each}
    {:else}
      <div class="text-muted-foreground text-sm">暂无日志</div>
    {/if}
  </div>
  <Button onclick={scrollToBottom} class="absolute top-4 right-4 z-10" variant="outline">Scroll</Button>
</ScrollArea>
