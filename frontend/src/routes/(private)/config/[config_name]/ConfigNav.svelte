<script lang="ts">
  import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "$lib/components/ui/accordion";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";
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
  const topicClient = useTopic("ConfigNav");

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // Derived state to transform raw topic data into a structured array for the UI.
  const navItems = $derived.by(() => {
    const serverData = topicClient.data as Record<string, Record<string, string>> | undefined;

    if (!serverData) return [] as NavItem[];

    return Object.entries(serverData).map(([navKey, navData]) => ({
      key: navKey,
      name: navData._info || navKey,
      cards: Object.entries(navData)
        .filter(([cardKey]) => cardKey !== "_info")
        .map(([cardKey, cardName]) => ({ key: cardKey, name: cardName })),
    }));
  });

  // --- Event Handlers ---
  function handleCardClick(clickedNavKey: string, clickedCardKey: string) {
    // Update internal state to reflect the last click.
    ui.card_name = clickedCardKey;
    ui.nav_name = clickedNavKey;

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
          handleCardClick(openedNavItem.key, openedNavItem.cards[0].key);
        }
      }
    }
  });
</script>

<nav class={cn("w-full", className)} aria-label="Configuration Navigation">
  <div class="flex flex-col space-y-1 px-3">
    <!-- 
      Overview and Device buttons has the same style as the accordion items, 
      and active indicator like nav items.
    -->
    <NavButton name="Overview" active={ui.isOverview} onclick={onOverviewClick} variant="root" />
    <NavButton name="Device" active={ui.isDevice} onclick={onDeviceClick} variant="root" />
  </div>

  {#if navItems.length}
    <!-- 
          Accordion's value is bound to our internal `nav_name` state.
          When a user clicks a trigger, `nav_name` is updated.
        -->
    <Accordion type="single" class="w-full" bind:value={ui.opened_nav}>
      {#each navItems as nav (nav.key)}
        <AccordionItem class="border-none" value={nav.key}>
          <AccordionTrigger class={cn("text-md px-3 py-2 pl-6 capitalize")}>
            {nav.name}
          </AccordionTrigger>
          <AccordionContent class="bg-accent border-y pt-1 pb-1">
            <div class="flex flex-col space-y-1 px-3">
              {#each nav.cards as card (card.key)}
                {@const active = card.key === ui.card_name && nav.key === ui.nav_name}
                <NavButton name={card.name} {active} onclick={() => handleCardClick(nav.key, card.key)} />
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
