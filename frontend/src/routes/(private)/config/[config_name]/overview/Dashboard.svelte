<script lang="ts">
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";
  import type { DashboardArgData } from "$lib/components/dashboard/utils";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";
  type $$Props = {
    class?: string;
  };
  let { class: className }: $$Props = $props();

  // --- WebSocket & RPC Setup ---
  type ConfigArgData = Record<string, Record<string, DashboardArgData>>;
  const topicClient = useTopic<ConfigArgData>("Dashboard");

  const itemList = $derived(topicClient.data ?? {});
</script>

<DashboardCard class={cn("neushadow bg-card rounded-lg", className)} items={itemList} />
