<script lang="ts">
  import { dev } from "$app/environment";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { Button } from "$lib/components/ui/button";
  import { ScrollArea } from "$lib/components/ui/scroll-area";
  import { t } from "$lib/i18n";
  import { HeaderContext } from "$lib/slotcontext.svelte";
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
  const debugNavItems = $derived(
    // /dev/debug/* routes are dropped from the production build by the
    // svelte-drop-dev-page plugin, so only show them in development
    dev
      ? [
          { path: "/dev/debug/ws", name: t.WebsocketTest.Title() },
          { path: "/dev/debug/workerstatus", name: "Worker Status" },
          { path: "/dev/debug/scheduler", name: "Scheduler" },
          { path: "/dev/debug/preview", name: "Preview" },
          { path: "/dev/debug/dashboard", name: "Dashboard" },
          { path: "/dev/debug/dashboardgroup", name: "Dashboard Group" },
          { path: "/dev/debug/log", name: "Log Viewer" },
          { path: "/dev/debug/configdisplay", name: "Config Display" },
        ]
      : [],
  );

  // --- Derived State ---
  const currentPath = $derived(page.url.pathname);
  function matchPath(path: string) {
    return currentPath === path || currentPath.startsWith(path + "/");
  }

  // --- Event Handlers ---
  async function handleNavClick(path: string) {
    await goto(path);
  }

  // header snippet
  const displayHeader = $derived.by(() => {
    for (const item of alasioNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    for (const item of devNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    for (const item of debugNavItems) {
      if (matchPath(item.path)) return item.name;
    }
    return currentPath;
  });
  HeaderContext.use(header);
</script>

{#snippet header()}
  <h1 class="w-full flex-1 text-center text-lg">{displayHeader}</h1>
{/snippet}

{#snippet navSection(title: string, items: typeof devNavItems)}
  <!-- This section's border-t aligns with AppHeader bottom:
       aside-item pt-1 (4px) + aside p-4 (16px) + h2 line-height (28px) = 48px = h-12 -->
  <div class="flex flex-col space-y-1">
    <h2 class="px-3 text-lg font-semibold">{title}</h2>
    <div class="border-border border-t"></div>
    {#each items as item (item.path)}
      {@const isActive = matchPath(item.path)}
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
    {#if dev}
      {@render navSection(t.DevTool.DebugTool(), debugNavItems)}
    {/if}
  </aside>
</ScrollArea>
