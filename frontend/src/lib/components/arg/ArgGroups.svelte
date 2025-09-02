<script lang="ts">
  import type { ArgData, InputProps } from "./types";
  import Arg from "./Arg.svelte";
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import { sizeObserver } from "$lib/use/size.svelte";

  type ArgGroupData = Record<string, ArgData>;
  type ArgGroupsProps = {
    data: Record<string, ArgGroupData>;
    handleEdit?: InputProps["handleEdit"];
    handleReset?: InputProps["handleReset"];
    class?: string;
  };
  let { data = $bindable(), handleEdit, handleReset, class: className }: ArgGroupsProps = $props();

  let containerSize = $state({ width: 0, height: 0 });
  const parentWidth = $derived(containerSize.width);
</script>

<div use:sizeObserver={containerSize} class={cn("space-y-2", className)}>
  {#each Object.entries(data || {}) as [groupKey, groupData] (groupKey)}
    {@const { _info, ...args } = groupData}
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
          {#each Object.entries(args) as [argKey] (argKey)}
            <Arg class="mt-3" bind:data={data[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} />
          {/each}
        </div>
      </Card.Content>
    </Card.Root>
  {/each}
</div>
