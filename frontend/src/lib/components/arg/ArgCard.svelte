<script lang="ts">
  import StaticDatetime from "../arginput/StaticDatetime.svelte";
  import PrettyValue from "../dashboard/PrettyValue.svelte";
  import Arg from "./Arg.svelte";
  import ArgGroupCard from "./ArgGroupCard.svelte";
  import CardEnable from "./CardEnable.svelte";
  import LayoutHorizontalLike from "./LayoutHorizontalLike.svelte";
  import {
    type ArgData,
    type CardData,
    type InfoData,
    type InputProps,
    getDashboardPairText,
    isDashboardInternalArg,
    isDashboardPairArg,
  } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    parentWidth: number;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    handleCardReset?: (data: InfoData) => void;
    flashing?: boolean;
    class?: string;
  };
  let {
    cardData = $bindable(),
    parentWidth,
    handleEdit,
    handleReset,
    handleCardReset,
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
        if (isDashboardInternalArg(argKey, dashboardType)) return false;
        // A Planner only displays its complete Value/Total pairs; custom
        // settings such as "Custom" stay in the model but out of the card.
        if (dashboardType === "Planner" && !isDashboardPairArg(argKey, groupData)) return false;
        return true;
      })
      .map(([argKey]) => argKey);
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
    <CardEnable bind:cardData {handleEdit} {handleReset} {handleCardReset} />
  {/snippet}
  <!-- Group args -->
  {#each Object.entries(Groups) as [groupKey, groupData]}
    <hr />
    {@const dashboardType = (groupData._info as ArgData | undefined)?.dashboard ?? ""}
    {@const dashboardArgs = getDashboardArgs(groupData, dashboardType)}
    {@const pairText = dashboardType === "Planner" ? getDashboardPairText(groupData) : ""}
    {#if dashboardType}
      <!-- Dashboard -->
      <div class="flex flex-col gap-y-1.5">
        <!-- Display dashboard value and time as a compact arg -->
        <LayoutHorizontalLike data={groupData._info as ArgData}>
          {#snippet InputSnippet()}
            <PrettyValue data={groupData} variant="primary" class="w-full text-left" />
          {/snippet}
          {#snippet PlaceholderSnippet()}
            <div class="flex flex-col gap-y-0.5">
              {#if pairText}
                <div class="text-muted-foreground truncate text-xs" title={pairText}>{pairText}</div>
              {/if}
              {#if groupData.Time}
                <StaticDatetime data={groupData.Time} class="justify-start" />
              {/if}
            </div>
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
