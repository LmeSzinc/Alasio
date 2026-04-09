<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { fastSmoothScroll, findScrollParent } from "$lib/use/scroll.svelte";
  import { elementSize, elementViewportSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import { untrack } from "svelte";
  import type UIState from "../../../routes/(private)/config/[config_name]/state.svelte";
  import Arg from "./Arg.svelte";
  import type { ArgData, InputProps } from "./utils.svelte";

  type ArgGroupsProps = {
    data: Record<string, Record<string, ArgData>>;
    indicateCard?: string;
    ui?: UIState;
    handleEdit?: InputProps["handleEdit"];
    handleReset?: InputProps["handleReset"];
    class?: string;
  };
  let { data = $bindable(), indicateCard, ui, handleEdit, handleReset, class: className }: ArgGroupsProps = $props();

  let containerSize = $state({ width: 0, height: 0 });
  const parentWidth = $derived(containerSize.width);

  // A reactive store for DOM element references.
  let groupElements = $state<Record<string, HTMLElement>>({});
  let root: HTMLElement | null = $state(null);
  let scrollParent = $derived(findScrollParent(root));

  const scrollHelper = fastSmoothScroll(() => scrollParent);

  // Effect to handle scrolling TO a card (when indicateCard is set from outside)
  $effect(() => {
    // This code runs whenever `ui.card_name`, `indicateCard` or `groupElements` changes.
    const target = ui ? ui.card_name : indicateCard;
    if (target && groupElements[target] && root) {
      const element = groupElements[target];
      const parent = scrollParent;

      if (parent) {
        // scroll-mt-6 is 1.5rem = 24px
        const top = element.offsetTop - 24;

        /**
         * 这里的 10 是一个阈值（Tolerance）。
         * 如果当前滚动位置与目标位置的差距在 10px 以内，就认为已经对齐了，
         * 这样可以防止微小的像素偏差导致反复触发滚动或者 UI 抖动。
         */
        if (Math.abs(parent.scrollTop - top) > 10) {
          scrollHelper.scrollTo(top);
        }
      } else {
        // Fallback
        element.scrollIntoView({
          block: "start",
          behavior: "smooth",
        });
      }
    }
  });

  // Track visible size of each group in the viewport
  let groupViewportSizes = $state<Record<string, { width: number; height: number }>>({});

  // Synchronize indicateCard with groupViewportSizes
  $effect(() => {
    if (scrollHelper.isScrolling) return;

    // Find the first group in the data order that has > 28px visible height
    const keys = Object.keys(data || {});
    const foundKey = keys.find((key) => (groupViewportSizes[key]?.height || 0) > 28);

    if (foundKey && ui) {
      if (foundKey !== untrack(() => ui.card_scroll)) {
        ui.card_scroll = foundKey;
        ui.card_indicate = foundKey;
      }
    }
  });

  // Dynamic card width based on content width
  // 1. follow parent width until max-w-180
  // 2. keep max-w-180, let margin auto increase, until card/parent <= 3/5
  // 3. keep width ratio card/parent <= 3/5 until max-w-240
  // 4. keep max-w-240, let margin auto increase
  const cardClass = $derived(parentWidth < 1200 ? "max-w-180" : parentWidth < 1600 ? "w-3/5" : "max-w-240");
</script>

<div bind:this={root} use:elementSize={containerSize} class={cn("relative space-y-4", className)}>
  {#each Object.entries(data || {}) as [groupKey, groupData]}
    {@const { _info, ...args } = groupData}
    <div
      bind:this={groupElements[groupKey]}
      use:elementViewportSize={{
        state: (groupViewportSizes[groupKey] ??= { width: 0, height: 0 }),
        root: scrollParent,
      }}
      data-group-key={groupKey}
      class="scroll-mt-6"
    >
      <Card.Root class={cn("neushadow mx-auto gap-0 border-none", cardClass)}>
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
              <Arg class="mt-2" bind:data={data[groupKey][argKey]} {parentWidth} {handleEdit} {handleReset} />
            {/each}
          </div>
        </Card.Content>
      </Card.Root>
    </div>
  {/each}
</div>
