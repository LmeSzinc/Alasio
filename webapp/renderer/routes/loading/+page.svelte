<script lang="ts">
  import { onMount } from "svelte";

  let logs = $state<string[]>([]);
  let logContainer: HTMLDivElement;

  onMount(() => {
    const unsubscribe = window.electronAPI.onBackendLog((log: string) => {
      logs.push(log);
      setTimeout(() => {
        if (logContainer) {
          logContainer.scrollTop = logContainer.scrollHeight;
        }
      }, 10);
    });

    return unsubscribe;
  });
</script>

<div class="flex h-screen flex-col items-center justify-center bg-background text-foreground">
  <h1 class="text-6xl font-bold mb-8">Alasio</h1>
  <div class="text-xl mb-12 text-muted-foreground">Starting backend...</div>

  <div
    bind:this={logContainer}
    class="w-[80%] max-w-4xl h-64 bg-muted rounded-lg p-4 overflow-y-auto font-mono text-sm border border-border"
  >
    {#each logs as log}
      <div class="whitespace-pre-wrap text-muted-foreground">{log}</div>
    {/each}
  </div>
</div>
