<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import Arg from "./Arg.svelte";
  import type { CardData, InputProps } from "./utils.svelte";

  type Props = {
    cardData: CardData;
    parentWidth: number;
    handleEdit: InputProps["handleEdit"];
    handleReset: InputProps["handleReset"];
    flashing?: boolean;
    isAdvance?: boolean;
    class?: string;
  };
  let {
    cardData = $bindable(),
    parentWidth,
    handleEdit,
    handleReset,
    flashing = false,
    isAdvance = false,
    class: className,
  }: Props = $props();

  const _info = $derived(cardData?._info);
  const groups = $derived.by(() => {
    const { _info, ...rest } = cardData || {};
    return rest;
  });
</script>

<Card.Root class={cn("neushadow mx-auto gap-0 border-none", flashing && "animate-flash-primary", className)}>
  <!-- Group name and help -->
  <Card.Header>
    {#if _info?.name}
      <Card.Title class="text-2xl font-bold">{_info.name}</Card.Title>
    {/if}
    {#if _info?.help}
      <Card.Description>{_info.help}</Card.Description>
    {/if}
  </Card.Header>
  <!-- Group args -->
  <Card.Content>
    {#each Object.entries(groups) as [groupKey, GroupData], i}
      <hr class={cn(i > 0 && "mt-2")} />
      <div class="">
        {#each Object.entries(GroupData) as [argKey]}
          <Arg
            class="mt-2"
            bind:data={cardData[groupKey][argKey]}
            {parentWidth}
            {handleEdit}
            {handleReset}
            {isAdvance}
          />
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
