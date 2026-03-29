<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { sizeObserver } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import Arg from "./Arg.svelte";
  import type { ArgData, InputProps } from "./utils.svelte";

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
  let root: HTMLElement | null = $state(null);
  $effect(() => {
    // This code runs whenever `indicateCard` or `groupElements` changes.
    if (indicateCard && groupElements[indicateCard] && root) {
      const element = groupElements[indicateCard];

      // Find the closest parent with overflow-auto or overflow-y-auto
      let scrollParent = root.parentElement;
      while (
        scrollParent &&
        getComputedStyle(scrollParent).overflowY !== "auto" &&
        getComputedStyle(scrollParent).overflowY !== "scroll"
      ) {
        scrollParent = scrollParent.parentElement;
      }

      if (scrollParent) {
        // scroll-mt-6 is 1.5rem = 24px
        const top = element.offsetTop - 24;
        scrollParent.scrollTo({
          top,
          behavior: "smooth",
        });
      } else {
        // Fallback
        element.scrollIntoView({
          block: "start",
          behavior: "smooth",
        });
      }
    }
  });
</script>

<div bind:this={root} use:sizeObserver={containerSize} class={cn("relative space-y-4", className)}>
  {#each Object.entries(data || {}) as [groupKey, groupData]}
    {@const { _info, ...args } = groupData}
    <div bind:this={groupElements[groupKey]} class="shadow-custom-complex scroll-mt-6">
      <Card.Root class="neushadow mx-auto max-w-180 gap-0 border-none">
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
