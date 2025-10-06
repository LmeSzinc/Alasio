<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { cn } from "$lib/utils.js";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    class?: string;
    onNavigate?: () => void;
  };

  let { class: className, onNavigate }: $$props = $props();

  // --- Navigation Items ---
  const navItems = [
    { path: "/dev/tools", name: "Tools" },
    { path: "/dev/assets", name: "Assets Manager" },
    { path: "/dev/ws", name: "WebSocket Test" },
  ];

  // --- Derived State ---
  // Get current pathname to determine active item
  const currentPath = $derived(page.url.pathname);

  // --- Event Handlers ---
  async function handleNavClick(path: string) {
    await goto(path);
    onNavigate?.();
  }
</script>

<aside
  class={cn("shadow-custom-complex w-full space-y-2 p-4", className)}
  role="navigation"
  aria-label="Main navigation"
>
  <h2 class="px-3 text-lg font-semibold">Dev tools</h2>
  <div class="border-t border-border"></div>
  <ScrollArea class="h-full w-full">
    <div class="flex flex-col space-y-1">
      {#each navItems as item (item.path)}
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
  </ScrollArea>
</aside>
