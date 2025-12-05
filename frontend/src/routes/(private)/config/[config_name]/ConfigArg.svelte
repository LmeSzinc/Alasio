<script lang="ts">
  import ArgGroups from "$lib/components/arg/ArgGroups.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    indicateCard?: string;
    class?: string;
  };
  let { indicateCard, class: className }: $$props = $props();

  // --- WebSocket & RPC Setup ---
  type ConfigArgData = Record<string, Record<string, ArgData>>;
  const topicClient = useTopic<ConfigArgData>("ConfigArg");
  const rpc = topicClient.rpc();

  // --- Reactive Logic (Svelte 5 Runes) ---

  // --- Event Handlers (passed down to ArgGroups) ---
  function handleEdit(data: ArgData) {
    rpc.call("set", {
      task: data.task,
      group: data.group,
      arg: data.arg,
      value: data.value,
    });
  }

  function handleReset(data: ArgData) {
    rpc.call("reset", {
      task: data.task,
      group: data.group,
      arg: data.arg,
    });
  }
</script>

<div class={cn("my-4 w-full", className)}>
  {#if topicClient.data}
    <ArgGroups bind:data={topicClient.data} {indicateCard} {handleEdit} {handleReset} />
  {/if}
</div>
