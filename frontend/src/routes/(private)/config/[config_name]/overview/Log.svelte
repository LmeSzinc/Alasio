<script lang="ts">
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  import LogData from "./LogData.svelte";
  import type { LogDataProps } from "./types";

  type Props = {
    class?: string;
  };
  let { class: className }: Props = $props();

  // Subscribe to Log topic
  const logClient = useTopic<LogDataProps[]>("Log");
  const logs = $derived(logClient.data || []);
</script>

<div class={cn("bg-card neushadow h-full w-full rounded-lg p-4 flex flex-col gap-1", className)}>
  <div class="">
    {#if logs.length === 0}
      <div class="text-muted-foreground text-sm">暂无日志</div>
    {:else}
      {#each logs as log}
        <LogData {...log} />
      {/each}
    {/if}
  </div>
</div>
