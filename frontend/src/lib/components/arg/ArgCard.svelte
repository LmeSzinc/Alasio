<script lang="ts">
  import StaticDatetime from "../arginput/StaticDatetime.svelte";
  import DashboardEdit from "../dashboard/DashboardEdit.svelte";
  import PrettyValue from "../dashboard/PrettyValue.svelte";
  import type { DashboardEditEvent } from "../dashboard/dashboardEdit";
  import Arg from "./Arg.svelte";
  import ArgGroupCard from "./ArgGroupCard.svelte";
  import CardEnable from "./CardEnable.svelte";
  import LayoutHorizontalLike from "./LayoutHorizontalLike.svelte";
  import { type ArgData, type CardData, type InfoData, type InputProps, getDashboardPairText } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    parentWidth: number;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    handleMultiEdit?: (events: DashboardEditEvent[]) => void;
    handleCardReset?: (data: InfoData) => void;
    flashing?: boolean;
    class?: string;
  };
  let {
    cardData = $bindable(),
    parentWidth,
    handleEdit,
    handleReset,
    handleMultiEdit,
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
    {@const pairText = dashboardType === "Planner" ? getDashboardPairText(groupData) : ""}
    {#if dashboardType}
      <!-- Dashboard: one summary row, every editable arg lives in the dialog -->
      <LayoutHorizontalLike data={groupData._info as ArgData}>
        {#snippet InputSnippet()}
          <!-- The record editor sits right of the value: its dialog edits every
               editable arg of the group (see dashboardFields) as one batch -->
          <div class="flex w-full min-w-0 items-center gap-x-1">
            <PrettyValue data={groupData} variant="primary" class="min-w-0 flex-1 text-left" />
            <DashboardEdit data={groupData} {handleEdit} {handleMultiEdit} />
          </div>
        {/snippet}
        {#snippet PlaceholderSnippet()}
          <div class="flex flex-col gap-y-0.5">
            {#if pairText}
              <div class="truncate text-xs text-muted-foreground" title={pairText}>{pairText}</div>
            {/if}
            {#if groupData.Time}
              <StaticDatetime data={groupData.Time} class="justify-start" />
            {/if}
          </div>
        {/snippet}
      </LayoutHorizontalLike>
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
