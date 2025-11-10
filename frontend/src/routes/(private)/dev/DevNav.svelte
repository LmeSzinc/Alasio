<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    class?: string;
  };

  let { class: className }: $$props = $props();

  // --- Navigation Items ---
  const alasioNavItems = [{ path: "/dev/config", name: "Config Manager" }];
  const devNavItems = [
    { path: "/dev/tools", name: "System Tools" },
    { path: "/dev/assets", name: "Assets Manager" },
    { path: "/dev/ws", name: "WebSocket Test" },
  ];

  // --- Derived State ---
  // Get current pathname to determine active item
  const currentPath = $derived(page.url.pathname);

  // --- Event Handlers ---
  async function handleNavClick(path: string) {
    await goto(path);
  }
</script>

{#snippet navSection(title: string, items: typeof devNavItems)}
  <div class="flex flex-col space-y-1">
    <h2 class="px-3 text-lg font-semibold">{title}</h2>
    <div class="border-border border-t"></div>
    {#each items as item (item.path)}
      {@const isActive = currentPath.startsWith(item.path)}
      <Button
        variant={isActive ? "default" : "ghost"}
        class="h-9 w-full justify-start px-3"
        onclick={() => handleNavClick(item.path)}
      >
        {item.name}
      </Button>
    {/each}
  </div>
{/snippet}

<ScrollArea class="h-full w-full">
  <aside class={cn("w-full space-y-4 p-4", className)} role="navigation" aria-label="Main navigation">
    {@render navSection("Alasio tools", alasioNavItems)}
    {@render navSection("Dev tools", devNavItems)}
  </aside>
</ScrollArea>
