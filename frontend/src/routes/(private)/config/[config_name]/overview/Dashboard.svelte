<script lang="ts">
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  type $$Props = {
    class?: string;
  };
  let { class: className }: $$Props = $props();

  // --- WebSocket & RPC Setup ---
  type ConfigArgData = Record<string, Record<string, Record<string, ArgData>>>;
  const topicClient = useTopic<ConfigArgData>("Dashboard");

  const itemList = $derived(topicClient.data ?? {});
</script>

<DashboardCard class={cn("bg-card rounded-lg", className)} items={itemList} />
