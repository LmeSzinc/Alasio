<script lang="ts">
  import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "$lib/components/ui/accordion";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    nav_name: string;
    card_name: string;
    opened_nav: string;
    // Callback: Fires when a card is clicked.
    onCardClick?: (nav_name: string, card_name: string) => void;
    // Optional class for custom styling.
    class?: string;
  };

  // Assign props to reactive variables, providing default empty functions for callbacks.
  let { nav_name, card_name, opened_nav = $bindable(), onCardClick, class: className }: $$props = $props();

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic("ConfigNav");

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // --- Reactive Logic (Svelte 5 Runes) ---

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
    card_name = clickedCardKey;
    nav_name = clickedNavKey;

    // Call the external callback with details.
    onCardClick?.(clickedNavKey, clickedCardKey);
  }

  // Auto-select the first card when a nav is opened
  $effect(() => {
    if (opened_nav) {
      // Find the nav that was just opened
      const openedNavItem = navItems.find((item) => item.key === opened_nav);

      // If the nav has cards and the current card_name is not in this nav, select the first card
      if (openedNavItem && openedNavItem.cards.length > 0) {
        const currentCardInNav = openedNavItem.cards.some((card) => card.key === card_name);

        // Only auto-select if the current card doesn't belong to this nav
        if (!currentCardInNav) {
          handleCardClick(openedNavItem.key, openedNavItem.cards[0].key);
        }
      }
    }
  });
</script>

<nav class={cn("shadow-custom-complex w-full space-y-2 px-3", className)} aria-label="Configuration Navigation">
  {#if navItems.length}
    <!-- 
          Accordion's value is bound to our internal `nav_name` state.
          When a user clicks a trigger, `nav_name` is updated.
        -->
    <Accordion type="single" class="w-full" bind:value={opened_nav}>
      {#each navItems as nav (nav.key)}
        <AccordionItem value={nav.key}>
          <AccordionTrigger class="text-md capitalize">{nav.name}</AccordionTrigger>
          <AccordionContent class="pl-2">
            <div class="flex flex-col space-y-1">
              {#each nav.cards as card (card.key)}
                <Button
                  variant={card.key === card_name
                    ? "default" // Indicated by parent: Primary theme color
                    : "ghost"}
                  class="h-9 w-full justify-start px-3"
                  onclick={() => handleCardClick(nav.key, card.key)}
                >
                  {card.name}
                </Button>
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
