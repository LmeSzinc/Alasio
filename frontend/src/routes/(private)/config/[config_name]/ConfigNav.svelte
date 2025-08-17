<!-- src/routes/config/[config_name]/ConfigNav.svelte -->
<script lang="ts">
  import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "$lib/components/ui/accordion";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";

  // --- Type definitions for callbacks ---
  type CardClickDetail = { navKey: string; cardKey: string };
  type NavClickCallback = (navKey: string) => void;
  type CardClickCallback = (detail: CardClickDetail) => void;

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    // Input: The name of the main configuration.
    config_name: string;
    // Input: The key of the card to be visually indicated (e.g., from URL or parent state).
    indicateCard?: string;
    // Callback: Fires when a card is clicked.
    onCardClick?: CardClickCallback;
    // Callback: Fires when a navigation accordion is opened.
    onNavClick?: NavClickCallback;
    // Optional class for custom styling.
    class?: string;
  };

  // Assign props to reactive variables, providing default empty functions for callbacks.
  let {
    config_name,
    indicateCard,
    onCardClick = () => {},
    onNavClick = () => {},
    class: className,
  }: $$props = $props();

  // --- Internal UI State (Svelte 5 Runes) ---
  let nav_name = $state<string>(""); // Key of the currently open accordion
  let card_name = $state<string>(""); // Key of the last clicked card

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic("ConfigNav");
  const stateClient = useTopic("ConnState");
  const rpc = stateClient.rpc();

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // --- Reactive Logic (Svelte 5 Runes) ---

  // Effect to call RPC when config_name changes.
  $effect(() => {
    if (config_name) {
      rpc.call("set_config", { name: config_name });
    }
  });

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

  // Effect to expand the correct accordion when `indicateCard` changes from the parent.
  $effect(() => {
    if (indicateCard) {
      const targetNav = navItems.find((nav) => nav.cards.some((card) => card.key === indicateCard));
      if (targetNav) {
        nav_name = targetNav.key;
      }
    }
  });

  // Effect to run the onNavClick callback when the user opens a new accordion.
  $effect(() => {
    // We only call the callback if nav_name is set to a valid string.
    if (nav_name) {
      onNavClick(nav_name);
    }
  });

  // --- Event Handlers ---
  function handleCardClick(clickedNavKey: string, clickedCardKey: string) {
    // Update internal state to reflect the last click.
    card_name = clickedCardKey;

    // The accordion's `bind:value` will automatically handle `nav_name` update on click.
    // We don't need to set `nav_name = clickedNavKey` here, but it's good practice
    // if a card click should force its parent nav open. Let's keep it.
    nav_name = clickedNavKey;

    // Call the external callback with details.
    onCardClick({ navKey: clickedNavKey, cardKey: clickedCardKey });
  }
</script>

<nav class={cn("w-full space-y-2 p-4", className)} aria-label="Configuration Navigation">
  <ScrollArea class="h-full w-full">
    {#if rpc.errorMsg}
      <p class="text-muted-foreground p-4 text-center text-sm">{rpc.errorMsg}</p>
    {:else if navItems.length}
      <!-- 
          Accordion's value is bound to our internal `nav_name` state.
          When a user clicks a trigger, `nav_name` is updated.
        -->
      <Accordion type="single" class="w-full" bind:value={nav_name}>
        {#each navItems as nav (nav.key)}
          <AccordionItem value={nav.key}>
            <AccordionTrigger class="text-md capitalize">{nav.name}</AccordionTrigger>
            <AccordionContent class="pl-2">
              <div class="flex flex-col space-y-1">
                {#each nav.cards as card (card.key)}
                  <Button
                    variant={card.key === indicateCard
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
    {/if}
  </ScrollArea>
</nav>
