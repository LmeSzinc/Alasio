<script lang="ts">
  import { fastSmoothScroll, findScrollParent } from "$lib/use/scroll.svelte";
  import { elementSize, elementViewportSize } from "$lib/use/size.svelte";
  import { cn } from "$lib/utils";
  import type UIState from "$private/config/[config_name]/state.svelte";
  import { untrack } from "svelte";
  import ArgCard from "./ArgCard.svelte";
  import type { CardData, InfoData, InputProps } from "./utils.svelte";

  type $$props = {
    data: Record<string, CardData>;
    indicateCard?: string;
    ui?: UIState;
    handleEdit?: InputProps["handleEdit"];
    handleReset?: InputProps["handleReset"];
    handleGroupReset?: (data: InfoData) => void;
    class?: string;
  };
  let {
    data = $bindable(),
    indicateCard,
    ui,
    handleEdit,
    handleReset,
    handleGroupReset,
    class: className,
  }: $$props = $props();

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
  let lastScrollTrigger = -1;

  // Effect to handle scrolling TO a card (when indicateCard is set from outside)
  $effect(() => {
    // This code runs whenever `ui.card_name`, `ui.scroll_trigger`, `indicateCard` or `groupElements` changes.
    const target = ui ? ui.card_name : indicateCard;
    const trigger = ui?.scroll_trigger; // subscribe to trigger

    if (target && groupElements[target] && root) {
      const element = groupElements[target];
      const parent = scrollParent;
      const isNavSwitchScroll = trigger === 0;

      if (parent) {
        // scroll-mt-6 is 1.5rem = 24px
        const top = element.offsetTop - 24;
        const isManual = trigger !== undefined && trigger !== lastScrollTrigger;
        lastScrollTrigger = trigger ?? -1;
        if (isManual) {
          // If nav is switched, scroll to top immediately, otherwise use smooth scroll
          const d = isNavSwitchScroll ? 0 : 250;
          scrollHelper.scrollTo(top, d);
        }
      } else {
        // Fallback
        element.scrollIntoView({
          block: "start",
          behavior: isNavSwitchScroll ? "instant" : "smooth",
        });
      }
    }
  });

  // Track visible size of each group in the viewport
  let groupViewportSizes = $state<Record<string, { width: number; height: number }>>({});

  // Synchronize indicateCard with groupViewportSizes
  $effect(() => {
    if (!ui) return;
    const keys = Object.keys(data || {});

    // calculate foundKey before early return of isScrolling, so groupViewportSizes[key] gets referenced in effect
    // Find the first group in the data order that has > 68px visible height
    // (card gap space-y-4) + (card bottom py-6) + (last arg min-h-7) = 68px
    const foundKey = keys.find((key) => (groupViewportSizes[key]?.height || 0) > 68);

    // MUST untrack scrollHelper.isScrolling
    // when having multiple cards at list bottom, user clicks the last card,
    // if tracking scrollHelper.isScrolling, card_indicate will quickly switch to the card at viewport top instead of the clicked card
    if (untrack(() => scrollHelper.isScrolling)) return;

    if (foundKey) {
      untrack(() => {
        const card_scroll = ui.card_scroll;
        if (foundKey !== card_scroll) {
          ui.card_scroll = foundKey;
          ui.card_indicate = foundKey;
        }
      });
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
  {#each Object.entries(data || {}) as [cardKey] (cardKey)}
    <div
      bind:this={groupElements[cardKey]}
      use:elementViewportSize={{
        onChange: (width, height) => {
          groupViewportSizes[cardKey] = { width, height };
        },
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
        {handleGroupReset}
        flashing={flashingCard === cardKey}
        class={cardClass}
      />
    </div>
  {/each}
</div>
