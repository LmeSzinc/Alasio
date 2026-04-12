<script lang="ts">
  import { fastSmoothScroll, findScrollParent } from "$lib/use/scroll.svelte";
  import { elementSize, elementViewportSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import type UIState from "$private/config/[config_name]/state.svelte";
  import { untrack } from "svelte";
  import ArgCard from "./ArgCard.svelte";
  import type { CardData, InputProps } from "./utils.svelte";

  type $$props = {
    data: Record<string, CardData>;
    indicateCard?: string;
    ui?: UIState;
    handleEdit?: InputProps["handleEdit"];
    handleReset?: InputProps["handleReset"];
    class?: string;
  };
  let { data = $bindable(), indicateCard, ui, handleEdit, handleReset, class: className }: $$props = $props();

  let containerSize = $state({ width: 0, height: 0 });
  const parentWidth = $derived(containerSize.width);

  // A reactive store for DOM element references.
  let groupElements = $state<Record<string, HTMLElement>>({});
  let flashingCard = $state("");

  // Effect to trigger flash
  $effect(() => {
    const target = ui?.flash_target;
    ui?.flash_trigger; // subscribe to trigger
    if (target) {
      // Restart animation if already flashing
      flashingCard = "";
      // Use requestAnimationFrame to ensure the class is removed before re-adding
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          flashingCard = target;
        });
      });
      // Clear after animation finishes (800ms)
      const timeout = setTimeout(() => {
        flashingCard = "";
      }, 800);
      return () => clearTimeout(timeout);
    }
  });

  let root: HTMLElement | null = $state(null);
  let scrollParent = $derived(findScrollParent(root) ?? undefined);

  const scrollHelper = fastSmoothScroll(() => scrollParent ?? null);

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
    if (!ui) return;
    // calculate foundKey before early return of isScrolling, so groupViewportSizes[key] gets referenced in effect
    // Find the first group in the data order that has > 28px visible height
    const keys = Object.keys(data || {});
    const foundKey = keys.find((key) => (groupViewportSizes[key]?.height || 0) > 28);

    if (untrack(() => scrollHelper.isScrolling)) return;

    if (foundKey) {
      const card_scroll = untrack(() => ui.card_scroll);
      if (foundKey !== card_scroll) {
        ui.card_scroll = foundKey;
        ui.card_indicate = foundKey;
      }
    }
  });

  // Effect to initialize groupViewportSizes for every group in data
  $effect(() => {
    if (!data) return;
    for (const key in data) {
      if (!groupViewportSizes[key]) {
        groupViewportSizes[key] = { width: 0, height: 0 };
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
  {#each Object.entries(data || {}) as [cardKey]}
    <div
      bind:this={groupElements[cardKey]}
      use:elementViewportSize={{
        state: groupViewportSizes[cardKey],
        root: scrollParent,
      }}
      data-group-key={cardKey}
      class="scroll-mt-6"
    >
      <ArgCard
        bind:cardData={data[cardKey]}
        {parentWidth}
        {handleEdit}
        {handleReset}
        flashing={flashingCard === cardKey}
        class={cardClass}
      />
    </div>
  {/each}
</div>
