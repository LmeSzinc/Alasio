<script lang="ts">
  import { cn } from "$lib/utils";
  import { getArgName } from "../arg/utils.svelte";
  import ColorDot from "./ColorDot.svelte";
  import PrettyValue from "./PrettyValue.svelte";
  import ReadableTime from "./ReadableTime.svelte";
  import type { DashboardArgData } from "./utils";

  let { data, class: className }: { data: DashboardArgData; class?: string } = $props();

  const time = $derived(data.value?.Time?.value ?? "");
</script>

<div class={cn("flex w-42 shrink-0 items-stretch gap-2.5 overflow-hidden pl-2", className)}>
  <div class="mt-2"><ColorDot color={data.dashboard_color ?? "#777"} class="h-2 w-2 rounded-full" /></div>

  <div class="flex min-w-0 flex-1 flex-col gap-0.5">
    <PrettyValue {data} />
    <div class="text-muted-foreground flex items-center gap-0.5 text-xs whitespace-nowrap">
      <span class="truncate" title={getArgName(data)}>
        {getArgName(data)}
      </span>
      <span class="shrink-0 opacity-50">-</span>
      <ReadableTime {time} class="" />
    </div>
  </div>
</div>
