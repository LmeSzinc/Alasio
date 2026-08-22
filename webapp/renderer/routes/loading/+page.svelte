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

<div class="bg-background text-foreground flex h-screen flex-col items-center justify-center">
  <h1 class="mb-8 text-6xl font-bold">Alasio</h1>
  <div class="text-muted-foreground mb-12 text-xl">Starting backend...</div>

  <div
    bind:this={logContainer}
    class="bg-muted border-border h-64 w-[80%] max-w-4xl overflow-y-auto rounded-lg border p-4 font-mono text-sm"
  >
    {#each logs as log}
      <div class="text-muted-foreground whitespace-pre-wrap">{log}</div>
    {/each}
  </div>
</div>
