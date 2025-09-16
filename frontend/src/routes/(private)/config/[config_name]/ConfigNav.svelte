<!-- src/routes/config/[config_name]/ConfigNav.svelte -->
<script lang="ts">
  import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "$lib/components/ui/accordion";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";
  import { useTopic } from "$lib/ws";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    // Input: The name of the main configuration.
    config_name: string;
    // Callback: Fires when a navigation accordion is opened.
    onNavClick?: (nav_name: string) => void;
    // Callback: Fires when a card is clicked.
    onCardClick?: (nav_name: string, card_name: string) => void;
    // Optional class for custom styling.
    class?: string;
  };

  // Assign props to reactive variables, providing default empty functions for callbacks.
  let { config_name, onNavClick, onCardClick, class: className }: $$props = $props();

  // --- Internal UI State (Svelte 5 Runes) ---
  let nav_name = $state<string>("");
  let opened_nav = $state<string>("");
  let card_name = $state<string>("");

  // --- WebSocket & RPC Setup ---
  const topicClient = useTopic("ConfigNav");
  const stateClient = useTopic("ConnState");
  const configRpc = stateClient.resilientRpc();
  const navRpc = stateClient.resilientRpc()

  // --- Data Types ---
  type CardItem = { key: string; name: string };
  type NavItem = { key: string; name: string; cards: CardItem[] };

  // --- Reactive Logic (Svelte 5 Runes) ---

  // Effect to call RPC when config_name changes.
  $effect(() => {
    if (config_name) {
      configRpc.call("set_config", { name: config_name });
      nav_name = "";
      card_name = "";
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

  // Effect to run the onNavClick callback when the user opens a new accordion.
  $effect(() => {
    if (nav_name) {
      navRpc.call("set_nav", { name: nav_name });
      onNavClick?.(nav_name);
    }
  });

  // --- Event Handlers ---
  function handleCardClick(clickedNavKey: string, clickedCardKey: string) {
    // Update internal state to reflect the last click.
    card_name = clickedCardKey;
    nav_name = clickedNavKey;

    // Call the external callback with details.
    onCardClick?.(clickedNavKey, clickedCardKey);
  }
</script>

<nav class={cn("w-full space-y-2 p-4 shadow-custom-complex", className)} aria-label="Configuration Navigation">
  <ScrollArea class="h-full w-full">
    {#if configRpc.errorMsg}
      <p class="text-muted-foreground p-4 text-center text-sm">{configRpc.errorMsg}</p>
    {:else if navItems.length}
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
    {/if}
  </ScrollArea>
</nav>
