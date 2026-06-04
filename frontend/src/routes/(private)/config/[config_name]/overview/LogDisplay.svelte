<script lang="ts">
  import Button from "$lib/components/ui/button/button.svelte";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils";
  import { ArrowDownToLine } from "@lucide/svelte";
  import LogData from "./LogData.svelte";
  import type { LogDataProps } from "./types";

  type Props = {
    data?: LogDataProps[];
    class?: string;
  };
  let { data: logData, class: className }: Props = $props();
  let logContainer: HTMLDivElement | null = $state(null);
  let isInitial = $state(true);

  // auto scroll on data changes
  let scrollRAF: number | null = null;
  function scrollToBottom() {
    scrollRAF = null;
    if (logContainer) {
      logContainer.scrollTop = logContainer.scrollHeight;
      logContainer.scrollLeft = 0;
      if (isInitial && logData && logData.length > 0) {
        isInitial = false;
      }
    }
  }
  $effect(() => {
    // tracking the length of logs
    const length = logData?.length ?? 0;
    if (logContainer && scrollRAF === null && length > 0) {
      scrollRAF = requestAnimationFrame(scrollToBottom);
    }
  });
</script>

<ScrollArea
  class={cn("bg-card neushadow group @container relative h-full max-h-screen w-full rounded-lg px-2.5 py-4", className)}
  orientation="both"
  bind:viewportRef={logContainer}
>
  <div class={cn("flex flex-col", isInitial && logData && logData.length > 0 && "invisible")}>
    {#if logData}
      {#each logData as log (log)}
        <LogData {...log} />
      {/each}
    {:else}
      <div class="text-muted-foreground text-sm">暂无日志</div>
    {/if}
  </div>
  <Button
    onclick={scrollToBottom}
    class="bg-card absolute top-4 right-4 z-10 opacity-0 transition-opacity group-hover:opacity-100"
    size="icon-sm"
    variant="outline"
  >
    <ArrowDownToLine />
  </Button>
</ScrollArea>
