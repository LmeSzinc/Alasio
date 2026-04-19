<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import Arg from "./Arg.svelte";
  import CardEnable from "./CardEnable.svelte";
  import I18nText from "./I18nText.svelte";
  import type { CardData, InfoData, InputProps } from "./utils.svelte";

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
</script>

<Card.Root class={cn("neushadow relative mx-auto gap-0 border-none", flashing && "animate-flash-primary", className)}>
  <!-- Group name and help -->
  <Card.Header class="flex flex-col gap-y-1.5">
    <!-- Group name -->
    {@const InfoName = Info?.name || "UnknownGroupName"}
    {@const InfoHelp = Info?.help}
    <div class="flex w-full items-center justify-between gap-x-4">
      <Card.Title class="flex-1 text-2xl font-bold">{InfoName}</Card.Title>
    </div>
    {#if Object.keys(SchedulerRest).length > 0 || InfoHelp}
      <div class="flex w-full flex-col gap-y-1">
        <!-- Other scheduler args -->
        {#if Object.keys(SchedulerRest).length > 0}
          <div class="flex w-full flex-col gap-y-1">
            {#each Object.entries(SchedulerRest) as [argKey]}
              <Arg bind:data={cardData.Scheduler[argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
            {/each}
          </div>
        {/if}
        <!-- Group help -->
        {#if InfoHelp}
          <Card.Description>
            <I18nText text={InfoHelp} />
          </Card.Description>
        {/if}
      </div>
    {/if}
    <CardEnable bind:cardData {handleEdit} {handleReset} {handleGroupReset} />
  </Card.Header>
  <!-- Group args -->
  <Card.Content class="flex flex-col gap-y-2 pt-2">
    {#each Object.entries(Groups) as [groupKey, GroupData]}
      <hr />
      <div class="flex flex-col gap-y-1.5">
        {#each Object.entries(GroupData) as [argKey]}
          <Arg bind:data={cardData[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} {isAdvanced} />
        {/each}
      </div>
    {/each}
  </Card.Content>
</Card.Root>

<style>
  @keyframes flash-primary {
    0%,
    40%,
    80%,
    100% {
      outline-color: transparent;
    }
    20%,
    60% {
      outline-color: var(--primary);
    }
  }

  :global(.animate-flash-primary) {
    outline: 2px solid transparent;
    outline-offset: -2px;
    animation: flash-primary 0.8s ease-in-out;
    /* Ensure it doesn't take space */
    position: relative;
    z-index: 10;
  }
</style>
