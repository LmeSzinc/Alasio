<script lang="ts">
  import { goto } from "$app/navigation";
  import type { ConfigTopicLike, WORKER_STATE } from "$lib/components/aside/types";
  import { Scheduler, type TaskQueueData } from "$lib/components/scheduler";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { NavContext } from "$lib/slotcontext.svelte";
  import { useTopic } from "$lib/ws";
  import { onDestroy } from "svelte";
  import ConfigNav from "./ConfigNav.svelte";
  import { uiState as ui } from "./state.svelte";

  // nav context
  NavContext.use(nav);

  let { data, children } = $props();
  const config_name = $derived(data.config_name);

  // topic
  const configClient = useTopic<ConfigTopicLike>("ConfigScan");
  const stateClient = useTopic("ConnState");
  const configRpc = stateClient.resilientRpc();
  const configResetRpc = stateClient.rpc();
  const navRpc = stateClient.resilientRpc();

  // Effect to call RPC when config_name changes.
  $effect(() => {
    if (config_name) {
      configRpc.call("set_config", { name: config_name });
      ui.setOverview();
    }
  });

  // Auto redirect when config not found
  $effect(() => {
    if (configRpc.errorMsg && configRpc.errorMsg.startsWith("RpcValueError: No such config")) {
      const serverData = configClient.data;
      if (serverData === undefined) {
        return;
      }
      const configs = Object.values(serverData);
      if (configs.length > 0) {
        configs.sort((a, b) => {
          if (a.gid !== b.gid) return a.gid - b.gid;
          return a.iid - b.iid;
        });
        goto(`/config/${configs[0].name}`, { replaceState: true });
      } else {
        goto("/dev/config", { replaceState: true });
      }
    }
  });
  // Clear nav state on page leave
  onDestroy(() => {
    configResetRpc.call("set_config", { name: "" });
  });

  function onCardClick(nav: string, card: string) {
    navRpc.call("set_nav", { name: nav });
    ui.setNav(nav, card);
    goto(`/config/${config_name}/arg`, { replaceState: true });
  }

  function onOverviewClick() {
    navRpc.call("set_nav", { name: "" });
    ui.setOverview();
    goto(`/config/${config_name}/overview`, { replaceState: true });
  }

  function onDeviceClick() {
    navRpc.call("set_nav", { name: "" });
    ui.setDevice();
    goto(`/config/${config_name}/device`, { replaceState: true });
  }

  // Auto select overview when opened_nav is empty
  $effect(() => {
    if (!ui.opened_nav) {
      onOverviewClick();
    }
  });

  // Scheduler
  const workerClient = useTopic<Record<string, WORKER_STATE> | undefined>("Worker");
  const state = $derived(workerClient.data?.[config_name] || "idle");

  const taskQueueClient = useTopic<TaskQueueData>("TaskQueue");
  const taskRunning = $derived(taskQueueClient.data?.running || undefined);
  const taskNext = $derived(
    [...(taskQueueClient.data?.pending || []), ...(taskQueueClient.data?.waiting || [])].filter(
      (task) => task.TaskName !== taskRunning,
    ),
  );
</script>

{#snippet nav()}
  <div class="flex h-full flex-col overflow-hidden gap-2">
    <Scheduler class="pb-0" {config_name} {state} {taskRunning} {taskNext} {onOverviewClick} {onDeviceClick} />
    <div class="border-border mx-3 border-t"></div>
    <ScrollArea class="min-h-0 w-full flex-1">
      <ConfigNav {onCardClick} {onOverviewClick} {onDeviceClick} />
    </ScrollArea>
  </div>
{/snippet}

{@render children()}
