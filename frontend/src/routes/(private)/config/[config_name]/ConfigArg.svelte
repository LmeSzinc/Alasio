<script lang="ts">
  import ArgGroups from "$lib/components/arg/ArgGroups.svelte";
  import type { ArgData } from "$lib/components/arg/types";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  // --- Type definitions ---
  type ArgGroupData = {
    _info: { name?: string; help?: string };
  } & {
    [argKey: string]: ArgData;
  };

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    class?: string;
  };

  let { class: className }: $$props = $props();

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic("ConfigArg");

  // --- Reactive Logic (Svelte 5 Runes) ---

  // --- Event Handlers (passed down to ArgGroups) ---
  function handleEdit(updatedArg: ArgData) {
    console.log("Arg edited, sending update to server:", updatedArg.value);
  }

  function handleReset(argToReset: ArgData) {
    console.log("Arg reset requested:", argToReset.name);
  }
</script>

<div class={cn("mt-2 h-full w-full", className)}>
  <ArgGroups bind:data={topicClient.data} {handleEdit} {handleReset} />
</div>
