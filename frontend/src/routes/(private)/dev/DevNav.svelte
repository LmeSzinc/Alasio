<script lang="ts">
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { t } from "$lib/i18n";
  import { cn } from "$lib/utils";

  // --- Props Definition (Svelte 5 Runes) ---
  type $$props = {
    class?: string;
  };

  let { class: className }: $$props = $props();

  // --- Navigation Items ---
  const alasioNavItems = $derived([{ path: "/dev/config", name: t.ConfigScan.ConfigManager() }]);
  const devNavItems = $derived([
    { path: "/dev/tools", name: t.DevTool.SystemTool() },
    { path: "/dev/assets", name: t.AssetManager.AssetManager() },
    { path: "/dev/compat", name: "Browser Compatibility" },
  ]);
  const debugNavItems = $derived([
    { path: "/dev/ws", name: t.WebsocketTest.Title() },
    { path: "/dev/workerstatus", name: "Worker Status" },
    { path: "/dev/scheduler", name: "Scheduler" },
  ]);

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
    {@render navSection(t.DevTool.AlasioTool(), alasioNavItems)}
    {@render navSection(t.DevTool.DevTool(), devNavItems)}
    {@render navSection(t.DevTool.DebugTool(), debugNavItems)}
  </aside>
</ScrollArea>
