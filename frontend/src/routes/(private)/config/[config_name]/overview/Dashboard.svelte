<script lang="ts">
  import type { ArgData } from "$lib/components/arg/utils.svelte";
  import DashboardCard from "$lib/components/dashboard/DashboardCard.svelte";
  import { cn } from "$lib/utils";
  import { useTopic } from "$lib/ws";

  type $Props = {
    class?: string;
  };
  let { class: className }: $Props = $props();

  // --- WebSocket & RPC Setup ---
  // The Dashboard topic view is keyed by card (`card-{task}-{group}`), which is a
  // nav structure detail without display meaning: the dashboard items are the
  // groups the view holds, whichever card carries them. So the view is flattened
  // into the flat `{item_name: {arg_name: ArgData}}` the dashboard displays.
  type ConfigArgData = Record<string, Record<string, Record<string, ArgData>>>;
  const topicClient = useTopic<ConfigArgData>("Dashboard");

  const itemList = $derived(
    Object.values(topicClient.data ?? ({} as ConfigArgData)).reduce<Record<string, Record<string, ArgData>>>(
      (items, card) => Object.assign(items, card),
      {},
    ),
  );
</script>

<DashboardCard class={cn("neushadow bg-card rounded-lg", className)} items={itemList} />
