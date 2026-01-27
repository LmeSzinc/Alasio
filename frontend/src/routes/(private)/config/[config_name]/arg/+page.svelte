<script lang="ts">
  import ArgGroups from "$lib/components/arg/ArgGroups.svelte";
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import { useTopic } from "$lib/ws";
  import { getContext } from "svelte";
  import UIState from "../state.svelte";

  // --- WebSocket & RPC Setup ---
  type ConfigArgData = Record<string, Record<string, ArgData>>;
  const topicClient = useTopic<ConfigArgData>("ConfigArg");
  const rpc = topicClient.rpc();

  const ui = getContext<UIState>("ui_state");

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

<div class="h-full w-full overflow-auto py-4">
  {#if topicClient.data}
    <ArgGroups
      class="w-full px-2.5"
      bind:data={topicClient.data}
      indicateCard={ui.card_name}
      {handleEdit}
      {handleReset}
    />
  {:else}
    <div class="text-muted-foreground text-center text-sm">No data</div>
  {/if}
</div>
