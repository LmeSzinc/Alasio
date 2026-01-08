<script lang="ts">
  import { goto } from "$app/navigation";
  import type { WORKER_STATUS } from "$lib/components/aside/types";
  import { Scheduler, type TaskQueueData } from "$lib/components/scheduler";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { NavContext } from "$lib/slotcontext.svelte";
  import { useTopic } from "$lib/ws";
  import { onDestroy, setContext } from "svelte";
  import ConfigNav from "./ConfigNav.svelte";
  import UIState from "./state.svelte";

  // nav context
  NavContext.use(nav);

  let { data, children } = $props();
  const config_name = $derived(data.config_name);

  // topic
  const stateClient = useTopic("ConnState");
  const configRpc = stateClient.resilientRpc();
  const configResetRpc = stateClient.rpc();
  const navRpc = stateClient.resilientRpc();

  // shared state among subpages
  const ui = new UIState();
  setContext("ui_state", ui);

  // Effect to call RPC when config_name changes.
  $effect(() => {
    if (config_name) {
      configRpc.call("set_config", { name: config_name });
      ui.nav_name = "";
      ui.card_name = "";
      ui.opened_nav = "";
    }
  });
  // Clear nav state on page leave
  onDestroy(() => {
    configResetRpc.call("set_config", { name: "" });
  });

  function onCardClick(nav: string, card: string) {
    navRpc.call("set_nav", { name: nav });
    ui.nav_name = nav;
    ui.card_name = card;
    goto(`/config/${config_name}/arg`, { replaceState: true });
  }

  function onOverviewClick() {
    navRpc.call("set_nav", { name: "" });
    ui.nav_name = "";
    ui.card_name = "";
    ui.opened_nav = "";
    goto(`/config/${config_name}/overview`, { replaceState: true });
  }

  function onDeviceClick() {
    navRpc.call("set_nav", { name: "" });
    ui.nav_name = "";
    ui.card_name = "";
    ui.opened_nav = "__nav_device__"; // special value to avoid going to overview
    goto(`/config/${config_name}/device`, { replaceState: true });
  }

  // Auto select overview when opened_nav is empty
  $effect(() => {
    if (!ui.opened_nav) {
      onOverviewClick();
    }
  });

  // Scheduler
  const workerClient = useTopic<Record<string, WORKER_STATUS> | undefined>("Worker");
  const status = $derived(workerClient.data?.[config_name] || "idle");

  const taskQueueClient = useTopic<TaskQueueData>("TaskQueue");
  const taskRunning = $derived(taskQueueClient.data?.running ?? undefined);
  const taskNext = $derived(
    [...(taskQueueClient.data?.pending || []), ...(taskQueueClient.data?.waiting || [])].filter(
      (task) => task.TaskName !== taskRunning?.TaskName,
    ),
  );
</script>

{#snippet nav()}
  <ScrollArea class="h-full w-full">
    <Scheduler {config_name} {status} {taskRunning} {taskNext} {onOverviewClick} {onDeviceClick} />
    <ConfigNav nav_name={ui.nav_name} card_name={ui.card_name} bind:opened_nav={ui.opened_nav} {onCardClick} />
  </ScrollArea>
{/snippet}

{@render children()}
