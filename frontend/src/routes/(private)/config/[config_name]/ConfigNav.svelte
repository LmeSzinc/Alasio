<script lang="ts">
  import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "$lib/components/ui/accordion";
  import { t } from "$lib/i18n";
  import { HeaderContext } from "$lib/slotcontext.svelte";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";
  import { untrack } from "svelte";
  import NavButton from "./NavButton.svelte";
  import { uiState as ui } from "./state.svelte";

  type $$props = {
    onCardClick?: (nav_name: string, card_name: string) => void;
    onOverviewClick?: () => void;
    onDeviceClick?: () => void;
    viewport?: HTMLElement | null;
    class?: string;
  };

  // Assign props to reactive variables, providing default empty functions for callbacks.
  let { onCardClick, onOverviewClick, onDeviceClick, viewport, class: className }: $$props = $props();

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic<Record<string, Record<string, string>>>("ConfigNav");

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // Derived state to transform raw topic data into a structured array for the UI.
  const navItems = $derived.by(() => {
    const navData = topicClient.data;

    if (!navData) return [] as NavItem[];

    return Object.entries(navData).map(([navKey, navData]) => {
      return {
        key: navKey,
        name: navData._info || navKey,
        cards: Object.entries(navData)
          .filter(([cardKey]) => cardKey !== "_info")
          .map(([cardKey, cardName]) => ({ key: cardKey, name: cardName })),
      };
    });
  });

  // --- Event Handlers ---
  function handleCardClick(clickedNavKey: string, clickedCardKey: string) {
    // Call the external callback with details.
    onCardClick?.(clickedNavKey, clickedCardKey);
  }

  // Auto-select the first card when a nav is opened
  $effect(() => {
    if (ui.opened_nav) {
      // Find the nav that was just opened
      const openedNavItem = navItems.find((item) => item.key === ui.opened_nav);

      // If the nav has cards and the indicator is not on this nav, select the first card.
      // Check nav_name instead of card_name: card keys may be shared between navs
      // (e.g. "停止条件" exists in main/gems/coalition), so card_name alone
      // cannot tell whether the indicator is on the opened nav.
      if (openedNavItem && openedNavItem.cards.length > 0) {
        const indicatorInNav =
          ui.nav_name === ui.opened_nav && openedNavItem.cards.some((card) => card.key === ui.card_indicate);

        // Only auto-select if the indicator is not on the opened nav
        if (!indicatorInNav) {
          untrack(() => {
            handleCardClick(openedNavItem.key, openedNavItem.cards[0].key);
          });
        }
      }
    }
  });

  // --- Auto scroll the opened nav into view ---
  // Expanding a nav may push its expanded content (nav title + all card titles)
  // out of the nav viewport. Scroll instantly to fit:
  // - item shorter than the viewport: align item bottom to viewport bottom
  // - item taller than the viewport: align item top to viewport top
  let itemElements: Record<string, HTMLElement> = $state({});

  function scrollNavToFit(navKey: string) {
    const item = itemElements[navKey];
    if (!item || !viewport) return;

    const itemTop = item.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop;
    const itemHeight = item.offsetHeight;
    const viewportHeight = viewport.clientHeight;

    let target: number;
    if (itemHeight >= viewportHeight) {
      // Content is taller than the viewport, align to the top
      target = itemTop;
    } else {
      // Show the full expanded item (bottom aligned)
      // Leave a 24px gap at the viewport bottom so the next nav below
      // stays visible, hinting that the expanded content has ended
      target = itemTop + itemHeight - viewportHeight + 24;
    }
    // No animation, scroll instantly
    const targetClamped = Math.max(0, target);
    viewport.scrollTop = targetClamped;
  }

  // The accordion content mounts without animation, measure after the DOM is laid out
  $effect(() => {
    const opened = ui.opened_nav;
    if (!opened || ui.isDevice) return;
    requestAnimationFrame(() => {
      scrollNavToFit(opened);
    });
  });

  // --- Header Snippet ---
  // Use the nav_name to display the current nav name in the header
  // If the nav_name is "Overview" or "Device", display "Overview" or "Device"
  // Otherwise, display the nav_name
  const displayHeader = $derived.by(() => {
    // reference topic data first
    const navData = topicClient.data;
    if (ui.isOverview) return t.Overview.OverviewTitle();
    if (ui.isDevice) return t.Device.DeviceTitle();
    return navData?.[ui.nav_name]?._info || ui.nav_name;
  });
  HeaderContext.use(header);
</script>

{#snippet header()}
  <h1 class="w-full flex-1 text-center text-lg">{displayHeader}</h1>
{/snippet}

<nav class={cn("w-full", className)} aria-label="Configuration Navigation">
  <div class="flex flex-col px-3">
    <!-- 
      Overview and Device buttons has the same style as the accordion items, 
      and active indicator like nav items.
      Keep height h-10
    -->
    <div class="py-1">
      <NavButton name="Overview" active={ui.isOverview} onclick={onOverviewClick} variant="root" />
    </div>
    <div class="py-1">
      <NavButton name="Device" active={ui.isDevice} onclick={onDeviceClick} variant="root" />
    </div>
  </div>

  {#if navItems.length}
    <!-- 
          Accordion's value is bound to our internal `nav_name` state.
          When a user clicks a trigger, `nav_name` is updated.
        -->
    <Accordion type="single" class="w-full" bind:value={ui.opened_nav}>
      {#each navItems as nav (nav.key)}
        <div bind:this={itemElements[nav.key]}>
          <AccordionItem class="border-none" value={nav.key}>
            <AccordionTrigger class={cn("text-md px-3 py-2 pl-6")}>
              {nav.name}
            </AccordionTrigger>
            <AccordionContent class="bg-accent border-y py-2">
              <div class="flex flex-col space-y-1 px-3">
                {#each nav.cards as card (card.key)}
                  {@const active = card.key === ui.card_indicate && nav.key === ui.nav_name}
                  <NavButton
                    name={card.name}
                    {active}
                    onclick={() => handleCardClick(nav.key, card.key)}
                    ondblclick={() => ui.triggerFlash(card.key)}
                  />
                {/each}
              </div>
            </AccordionContent>
          </AccordionItem>
        </div>
      {/each}
    </Accordion>
  {:else}
    <p>No data</p>
  {/if}
</nav>
