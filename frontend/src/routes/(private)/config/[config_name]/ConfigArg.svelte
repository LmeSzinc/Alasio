<script lang="ts">
  import ArgGroups from "$lib/components/arg/ArgGroups.svelte";
  import type { ArgData } from "$lib/components/arg/types";
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

  // --- Reactive Logic (Svelte 5 Runes) ---

  // --- Event Handlers (passed down to ArgGroups) ---
  function handleEdit(updatedArg: ArgData) {
    console.log("Arg edited, sending update to server:", updatedArg.value);
  }

  function handleReset(argToReset: ArgData) {
    console.log("Arg reset requested:", argToReset.name);
  }
</script>

<div class={cn("mt-2 w-full", className)}>
  {#if topicClient.data}
    <ArgGroups bind:data={topicClient.data} {indicateCard} {handleEdit} {handleReset} />
  {/if}
</div>
