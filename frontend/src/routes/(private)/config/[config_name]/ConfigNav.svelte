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
    class?: string;
  };

  // Assign props to reactive variables, providing default empty functions for callbacks.
  let { onCardClick, onOverviewClick, onDeviceClick, class: className }: $$props = $props();

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic<Record<string, Record<string, string>>>("ConfigNav");

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // Derived state to transform raw topic data into a structured array for the UI.
  const navItems = $derived.by(() => {
    const navData = topicClient.data;

    if (!navData) return [] as NavItem[];

    return Object.entries(navData).map(([navKey, navData]) => ({
      key: navKey,
      name: navData._info || navKey,
      cards: Object.entries(navData)
        .filter(([cardKey]) => cardKey !== "_info")
        .map(([cardKey, cardName]) => ({ key: cardKey, name: cardName })),
    }));
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

      // If the nav has cards and the current card_name is not in this nav, select the first card
      if (openedNavItem && openedNavItem.cards.length > 0) {
        const currentCardInNav = openedNavItem.cards.some((card) => card.key === ui.card_name);

        // Only auto-select if the current card doesn't belong to this nav
        if (!currentCardInNav) {
          untrack(() => {
            handleCardClick(openedNavItem.key, openedNavItem.cards[0].key);
          });
        }
      }
    }
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
      {/each}
    </Accordion>
  {:else}
    <p>No data</p>
  {/if}
</nav>
