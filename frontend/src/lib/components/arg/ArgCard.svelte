<script lang="ts">
  import StaticDatetime from "../arginput/StaticDatetime.svelte";
  import PrettyValue from "../dashboard/PrettyValue.svelte";
  import Arg from "./Arg.svelte";
  import ArgGroupCard from "./ArgGroupCard.svelte";
  import CardEnable from "./CardEnable.svelte";
  import LayoutHorizontalLike from "./LayoutHorizontalLike.svelte";
  import type { ArgData, CardData, InfoData, InputProps } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    parentWidth: number;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    handleGroupReset?: (data: InfoData) => void;
    flashing?: boolean;
    class?: string;
  };
  let {
    cardData = $bindable(),
    parentWidth,
    handleEdit,
    handleReset,
    handleGroupReset,
    flashing = false,
    class: className,
  }: Props = $props();

  const Info = $derived(cardData?._info);
  const SchedulerRest = $derived.by(() => {
    const { Enable, NextRun, ...rest } = cardData?.Scheduler || {};
    return rest;
  });
  const Groups = $derived.by(() => {
    const { _info, Scheduler, ...rest } = cardData || {};
    return rest;
  });

  let isAdvanced = $state(false);

  // Get extra args from a dashboard group
  function getDashboardArgs(groupData: Record<string, ArgData>, dashboardType: string): string[] {
    return Object.entries(groupData)
      .filter(([argKey, _]) => {
        return !isDashboardInternalArg(argKey, dashboardType);
      })
      .map(([argKey]) => argKey);
  }
  // Arg name "_info", "Value", "Time", "ServerUpdate" will be removed from the group
  // If dashboard type is "DynamicTotal", arg name "Total" will be removed
  function isDashboardInternalArg(argKey: string, dashboardType: string): boolean {
    if (argKey === "_info") return true;
    if (argKey === "Value" || argKey === "Time" || argKey === "ServerUpdate") return true;
    if (dashboardType === "DynamicTotal" && argKey === "Total") return true;
    return false;
  }
</script>

<ArgGroupCard title={Info?.name || "UnknownGroupName"} help={Info?.help} {flashing} class={className}>
  {#snippet headerExtra()}
    <!-- Other scheduler args -->
    {#if Object.keys(SchedulerRest).length > 0}
      <div class="flex w-full flex-col gap-y-1">
        {#each Object.entries(SchedulerRest) as [argKey]}
          <Arg bind:data={cardData.Scheduler[argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
        {/each}
      </div>
    {/if}
    <CardEnable bind:cardData {handleEdit} {handleReset} {handleGroupReset} />
  {/snippet}
  <!-- Group args -->
  {#each Object.entries(Groups) as [groupKey, groupData]}
    <hr />
    {@const dashboardType = (groupData._info as ArgData | undefined)?.dashboard ?? ""}
    {@const dashboardArgs = getDashboardArgs(groupData, dashboardType)}
    {#if dashboardType}
      <!-- Dashboard -->
      <div class="flex flex-col gap-y-1.5">
        <!-- Display dashboard value and time as a compact arg -->
        <LayoutHorizontalLike data={groupData._info as ArgData}>
          {#snippet InputSnippet()}
            <PrettyValue data={groupData} variant="primary" class="w-full text-left" />
          {/snippet}
          {#snippet PlaceholderSnippet()}
            {#if groupData.Time}
              <StaticDatetime data={groupData.Time} class="justify-start" />
            {/if}
          {/snippet}
        </LayoutHorizontalLike>
        <!-- Display extra dashboard args -->
        {#each dashboardArgs as argKey}
          <Arg bind:data={cardData[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
        {/each}
      </div>
    {:else}
      <!-- Normal group -->
      <div class="flex flex-col gap-y-1.5">
        {#each Object.entries(groupData) as [argKey]}
          <Arg bind:data={cardData[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
        {/each}
      </div>
    {/if}
  {/each}
</ArgGroupCard>
