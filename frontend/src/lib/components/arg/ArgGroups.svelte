<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { sizeObserver } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import Arg from "./Arg.svelte";
  import type { ArgData, InputProps } from "./types";

  type ArgGroupsProps = {
    data: Record<string, Record<string, ArgData>>;
    indicateCard?: string;
    handleEdit?: InputProps["handleEdit"];
    handleReset?: InputProps["handleReset"];
    class?: string;
  };
  let { data = $bindable(), indicateCard, handleEdit, handleReset, class: className }: ArgGroupsProps = $props();

  let containerSize = $state({ width: 0, height: 0 });
  const parentWidth = $derived(containerSize.width);

  // A reactive store for DOM element references.
  let groupElements = $state<Record<string, HTMLElement>>({});
  $effect(() => {
    // This code runs whenever `indicateCard` or `groupElements` changes.
    if (indicateCard && groupElements[indicateCard]) {
      const element = groupElements[indicateCard];
      element.scrollIntoView({
        block: "start",
      });
    }
  });
</script>

<div use:sizeObserver={containerSize} class={cn("space-y-2", className)}>
  {#each Object.entries(data || {}) as [groupKey, groupData]}
    {@const { _info, ...args } = groupData}
    <div bind:this={groupElements[groupKey]} class="scroll-mt-6">
      <Card.Root class="mx-auto max-w-180 gap-0">
        <!-- Group name and help -->
        <Card.Header>
          {#if _info?.name}
            <Card.Title class="text-2xl font-bold">{_info.name}</Card.Title>
          {/if}
          {#if _info?.help}
            <Card.Description>{_info.help}</Card.Description>
          {/if}
          <hr />
        </Card.Header>
        <!-- Group args -->
        <Card.Content>
          <div class="">
            {#each Object.entries(args) as [argKey]}
              <Arg class="mt-3" bind:data={data[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} />
            {/each}
          </div>
        </Card.Content>
      </Card.Root>
    </div>
  {/each}
</div>
