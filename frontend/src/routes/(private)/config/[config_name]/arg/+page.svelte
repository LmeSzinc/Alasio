<script lang="ts">
  import type { ArgData, CardData, InfoData } from "$lib/components/arg/utils.svelte";
  import { t } from "$lib/i18n";
  import { useTopic } from "$lib/ws";
  import ArgCardList from "$src/lib/components/arg/ArgCardList.svelte";
  import { untrack } from "svelte";
  import { toast } from "svelte-sonner";
  import { uiState as ui } from "../state.svelte";

  // --- WebSocket & RPC Setup ---
  type ConfigArgData = Record<string, CardData>;
  const topicClient = useTopic<ConfigArgData>("ConfigArg");
  const setRpc = topicClient.rpc();
  const resetRpc = topicClient.rpc();
  const groupResetRpc = topicClient.rpc();

  // --- Event Handlers (passed down to ArgCardList) ---
  function handleEdit(data: ArgData) {
    setRpc.call("set", {
      task: data.task,
      group: data.group,
      arg: data.arg,
      value: data.value,
    });
  }
  function handleReset(data: ArgData) {
    resetRpc.call("reset", {
      task: data.task,
      group: data.group,
      arg: data.arg,
    });
  }
  function handleGroupReset(data: InfoData) {
    groupResetRpc.call("group_reset", {
      card: data.card,
    });
  }

  const toastOptions = {
    duration: 2000,
    classes: {
      // Skip header height
      toast: "mt-10",
    },
  };
  $effect(() => {
    if (setRpc.successMsg) {
      untrack(() => {
        toast.success(t.Input.ConfigSet(), toastOptions);
      });
    }
  });
  $effect(() => {
    if (resetRpc.successMsg) {
      untrack(() => {
        toast.success(t.Input.ConfigReset(), toastOptions);
      });
    }
  });
  $effect(() => {
    if (groupResetRpc.successMsg) {
      untrack(() => {
        toast.success(t.Input.ConfigReset(), toastOptions);
      });
    }
  });
</script>

<div class="min-h-full w-full">
  {#if topicClient.data}
    <ArgCardList
      class="w-full px-2.5 py-4"
      bind:data={topicClient.data}
      {ui}
      {handleEdit}
      {handleReset}
      {handleGroupReset}
    />
  {:else}
    <div class="text-muted-foreground text-center text-sm">No data</div>
  {/if}
</div>
