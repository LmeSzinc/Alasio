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

  /**
   * True when the card holds an arg behind the advanced toggle: the scheduler
   * args and the rows of the normal groups. A dashboard group shows one
   * summary row and edits its args in a dialog, so it counts for nothing.
   */
  const hasAdvanced = $derived.by(() => {
    for (const groupData of Object.values(Groups)) {
      if ((groupData._info as ArgData | undefined)?.dashboard) continue;
      if (Object.values(groupData).some((arg) => arg.advanced)) return true;
    }
    return Object.values(SchedulerRest).some((arg) => arg.advanced);
  });

  /**
   * Entries of a group as displayed: an advanced arg stays hidden until the
   * user opens the advanced settings of the card.
   */
  function visibleEntries(groupData: Record<string, ArgData>) {
    return Object.entries(groupData).filter(([, arg]) => isAdvanced || !arg.advanced);
  }

  const SchedulerRows = $derived(visibleEntries(SchedulerRest));
</script>

<ArgGroupCard title={Info?.name || "UnknownGroupName"} help={Info?.help} {flashing} class={className}>
  {#snippet headerExtra()}
    <!-- Other scheduler args -->
    {#if SchedulerRows.length > 0}
      <div class="flex w-full flex-col gap-y-1">
        {#each SchedulerRows as [argKey]}
          <Arg bind:data={cardData.Scheduler[argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
        {/each}
      </div>
    {/if}
    <CardEnable bind:cardData {handleEdit} {handleReset} {handleCardReset} bind:isAdvanced {hasAdvanced} />
  {/snippet}
  <!-- Group args -->
  {#each Object.entries(Groups) as [groupKey, groupData]}
    {@const dashboardType = (groupData._info as ArgData | undefined)?.dashboard ?? ""}
    {@const pairText = dashboardType === "Planner" ? getDashboardPairText(groupData) : ""}
    <!-- A group whose rows are all behind the advanced toggle keeps no
         separator of its own while the toggle is closed -->
    {@const rows = dashboardType ? [] : visibleEntries(groupData)}
    {#if dashboardType || rows.length > 0}
      <hr />
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
          {#each rows as [argKey]}
            <Arg bind:data={cardData[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
          {/each}
        </div>
      {/if}
    {/if}
  {/each}
</ArgGroupCard>
